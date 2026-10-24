"""`mm!grupo`: várias contas do mesmo dono gerenciadas por uma só (`src.club_groups`).

- `mm!grupo` abre o painel: as contas na ordem (XI, formação, elenco, Memilets), os
  "reforços parados" (quem está no banco de uma conta e seria titular em outra) e os
  botões Otimizar, Escalar todas e Vender.
- `criar <nome>`, `convidar @conta`, `aceitar`/`recusar`, `sair`, `remover @conta`,
  `ordem @a @b @c`, `nome <nome>`, `desfazer`.
- `otimizar`: o melhor XI na Principal, o que sobra na Secundária… mostra o plano antes.
- `mover <jogador> <conta>`, `escalar`, `elenco [posição]` e `vender [conta] [filtro]`.

A conta é uma menção (`@conta`) ou a posição na ordem (`1`, `2`, `principal`, `secundaria`).
"""

from __future__ import annotations

import asyncio
from typing import Any

import discord
from discord.ext import commands

from src import club_groups, icons
from src.club_groups import GroupError, GroupPlan, rank_label
from src.cogs.common import ConfirmView, PlayerChoiceView, ambiguity_text, player_label
from src.cogs.trades import OwnerView, SalePanelView, quick_filter
from src.db import DATABASE_PATH, connect
from src.game import register_user, resolve_player
from src.teams import TeamError, auto_fill_lineup, equip_if_better
from src.transactions import TransactionError, sale_quote, sell_players

COLOR = discord.Color.from_rgb(88, 166, 255)
MOVE_LINES = 6  # reforços listados por conta no plano
IDLE_LINES = 8  # reforços parados no painel
ROSTER_LINES = 25


def account_name(member: dict[str, Any]) -> str:
    """`Principal · Time do Ana`."""
    team = member.get("team_name") or member.get("display_name") or "sem time"
    return f"{rank_label(member['rank'])} · {team}"


def _short(member: dict[str, Any] | None) -> str:
    return rank_label(member["rank"]) if member else "fora do grupo"


def _xi(value: float) -> str:
    return f"{value:.1f}".replace(".", ",")


def _member_by_id(group: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {m["discord_user_id"]: m for m in group["members"]}


def _counts(group: dict[str, Any], database_path) -> dict[str, int]:
    users = [m["discord_user_id"] for m in group["members"]]
    marks = ", ".join("?" for _ in users)
    with connect(database_path) as connection:
        return {
            row[0]: row[1]
            for row in connection.execute(
                f"SELECT discord_user_id, COUNT(*) FROM player_ownership "
                f"WHERE discord_user_id IN ({marks}) GROUP BY discord_user_id",
                users,
            )
        }


def overview_embed(group: dict[str, Any], plan: GroupPlan, database_path, lock: str | None):
    members = _member_by_id(group)
    counts = _counts(group, database_path)
    plans = {p.member["discord_user_id"]: p for p in plan.accounts}
    embed = discord.Embed(
        title=f"🤝 {group['name']}",
        description=(
            "Clubes separados, mesmo dono: cada um joga por si, e o dono gerencia os elencos "
            "por aqui."
        ),
        color=COLOR,
    )
    for member in group["members"]:
        user = member["discord_user_id"]
        account = plans.get(user)
        crown = " 👑" if user == group["owner_user_id"] else ""
        lines = [
            (
                f"XI **{_xi(account.before)}** · {member['formation']}"
                if account
                else "⚠️ Ainda sem time montado (`mm!time`)"
            ),
            f"{counts.get(user, 0)} jogadores · {icons.memilets(member.get('memilets') or 0)}",
        ]
        embed.add_field(
            name=f"{member['rank']}. {account_name(member)}{crown}"[:256],
            value="\n".join(lines),
            inline=True,
        )
    if plan.moves:
        lines = [
            f"**{move.player['name']}** {move.player['overall']} {move.player['game_position']} · "
            f"{_short(members.get(move.from_id))} → {_short(members.get(move.to_id))}"
            for move in plan.moves[:IDLE_LINES]
        ]
        if len(plan.moves) > IDLE_LINES:
            lines.append(f"… e mais {len(plan.moves) - IDLE_LINES}")
        gain = sum(max(0.0, a.after - a.before) for a in plan.accounts)
        embed.add_field(
            name=f"🔁 Reforços parados ({len(plan.moves)}) · XI somados +{_xi(gain)}",
            value="\n".join(lines)[:1024],
            inline=False,
        )
    else:
        embed.add_field(
            name="✅ Tudo no lugar",
            value="Nenhum jogador de uma conta seria titular numa conta acima dela.",
            inline=False,
        )
    if lock:
        embed.add_field(name="​", value=lock, inline=False)
    embed.set_footer(text="mm!grupo otimizar · mover · vender · escalar · elenco · ordem")
    return embed


def plan_embed(plan: GroupPlan, lock: str | None) -> discord.Embed:
    members = _member_by_id(plan.group)
    embed = discord.Embed(
        title=f"🔁 Otimizar · {plan.group['name']}",
        description=(
            "O melhor XI possível na Principal, o que sobra na Secundária, e assim por diante. "
            "Só muda de conta quem vira titular; os outros ficam onde estão."
            if plan.moves
            else "Nada a mudar: cada conta já tem o melhor time possível nessa ordem."
        ),
        color=COLOR,
    )
    for account in plan.accounts:
        diff = account.after - account.before
        sign = "+" if diff >= 0 else "−"
        lines = [f"XI {_xi(account.before)} → **{_xi(account.after)}** ({sign}{_xi(abs(diff))})"]
        for move in account.incoming[:MOVE_LINES]:
            player = move.player
            lines.append(
                f"⬅️ **{player['name']}** {player['overall']} {move.position} · "
                f"da {_short(members.get(move.from_id))}"
            )
        if len(account.incoming) > MOVE_LINES:
            lines.append(f"… e mais {len(account.incoming) - MOVE_LINES} chegando")
        if account.outgoing:
            lines.append(f"➡️ cede {account.outgoing}")
        embed.add_field(
            name=f"{account.member['rank']}. {account_name(account.member)}"[:256],
            value="\n".join(lines)[:1024],
            inline=False,
        )
    if plan.skipped:
        embed.add_field(
            name="Fora da conta",
            value=", ".join(account_name(m) for m in plan.skipped) + ": ainda sem time.",
            inline=False,
        )
    if lock:
        embed.add_field(name="​", value=lock, inline=False)
    embed.set_footer(
        text="Quem sai de uma conta perde capitão e cobranças lá. Jogador 🔒 vai junto, "
        "protegido; carta de pacote travada fica."
    )
    return embed


class PlanView(ConfirmView):
    """Aplicar ou cancelar o plano de otimização."""

    def __init__(self, author_id: int, plan: GroupPlan, database_path, *, locked: bool):
        async def apply(interaction: discord.Interaction) -> None:
            try:
                moved = await asyncio.to_thread(
                    club_groups.apply_plan, interaction.user.id, plan, database_path
                )
            except GroupError as error:
                await interaction.response.edit_message(content=str(error), embed=None, view=None)
                return
            lines = [
                f"{a.member['rank']}. {account_name(a.member)}: XI **{_xi(a.after)}**"
                for a in plan.accounts
            ]
            await interaction.response.edit_message(
                content=f"✅ Grupo otimizado: {moved} jogador(es) mudaram de conta e os "
                "times foram escalados.\n" + "\n".join(lines),
                embed=None,
                view=None,
            )

        super().__init__(
            author_id,
            apply,
            confirm_label=f"Aplicar ({len(plan.moves)} transferências)",
            cancel_text="Otimização cancelada; nada mudou.",
            timeout=300,
        )
        self.confirm.disabled = locked or not plan.moves
        if not locked and plan.moves:
            self.confirm.style = discord.ButtonStyle.success


class AccountSaleSelect(discord.ui.Select):
    """Escolhe a conta do grupo e abre o painel de venda dela."""

    def __init__(self, group: dict[str, Any], database_path):
        self.group = group
        self.database_path = database_path
        super().__init__(
            placeholder="Vender jogadores de qual conta?",
            options=[
                discord.SelectOption(label=account_name(m)[:100], value=m["discord_user_id"])
                for m in group["members"]
            ],
            row=1,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        member = _member_by_id(self.group)[self.values[0]]
        view = SalePanelView(
            interaction.user.id,
            self.database_path,
            account_id=member["discord_user_id"],
            account_label=account_name(member),
        )
        await interaction.response.send_message(embed=view.embed(), view=view)
        view.message = await interaction.original_response()


class GroupPanel(OwnerView):
    """Painel do grupo. Os botões de gerência só aparecem para o dono."""

    def __init__(self, cog: "GroupsCog", user: discord.abc.User, group: dict[str, Any]):
        super().__init__(user.id, timeout=600)
        self.cog = cog
        self.group = group
        if group["owner_user_id"] == str(user.id):
            for label, emoji, callback, style in (
                ("Otimizar", "🔁", self._optimize, discord.ButtonStyle.primary),
                ("Escalar todas", "📋", self._lineups, discord.ButtonStyle.secondary),
            ):
                button = discord.ui.Button(label=label, emoji=emoji, style=style, row=0)
                button.callback = callback
                self.add_item(button)
            self.add_item(AccountSaleSelect(group, cog.database_path))
        refresh = discord.ui.Button(label="Atualizar", emoji="🔄", row=0)
        refresh.callback = self._refresh
        self.add_item(refresh)

    async def _refresh(self, interaction: discord.Interaction) -> None:
        group = club_groups.group_of(interaction.user.id, self.cog.database_path)
        if group is None:
            await interaction.response.edit_message(
                content="Você não está mais nesse grupo.", embed=None, view=None
            )
            return
        self.group = group
        embed = await self.cog.overview(group)
        await interaction.response.edit_message(embed=embed, view=self)

    async def _optimize(self, interaction: discord.Interaction) -> None:
        embed, view = await self.cog.plan_message(interaction.user.id, self.group)
        await interaction.response.send_message(embed=embed, view=view)
        view.message = await interaction.original_response()

    async def _lineups(self, interaction: discord.Interaction) -> None:
        text = await self.cog.fill_all(self.group)
        self.group = club_groups.group_of(interaction.user.id, self.cog.database_path)
        await interaction.response.edit_message(
            content=text, embed=await self.cog.overview(self.group), view=self
        )


class GroupsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.database_path = getattr(bot, "database_path", DATABASE_PATH)

    # Ajudantes ------------------------------------------------------------------------------

    async def overview(self, group: dict[str, Any]) -> discord.Embed:
        users = [m["discord_user_id"] for m in group["members"]]
        plan = await asyncio.to_thread(club_groups.optimize_plan, group, self.database_path)
        lock = club_groups.transfer_lock(users, self.database_path)
        return overview_embed(group, plan, self.database_path, lock)

    async def plan_message(self, user_id: int, group: dict[str, Any]):
        users = [m["discord_user_id"] for m in group["members"]]
        plan = await asyncio.to_thread(club_groups.optimize_plan, group, self.database_path)
        lock = club_groups.transfer_lock(users, self.database_path) if plan.moves else None
        view = PlanView(user_id, plan, self.database_path, locked=bool(lock))
        return plan_embed(plan, lock), view

    async def fill_all(self, group: dict[str, Any]) -> str:
        lines = []
        for member in group["members"]:
            try:
                changed = await asyncio.to_thread(
                    auto_fill_lineup, member["discord_user_id"], self.database_path
                )
            except TeamError:
                lines.append(f"{member['rank']}. {account_name(member)}: sem time montado")
                continue
            lines.append(
                f"{member['rank']}. {account_name(member)}: "
                + (f"{changed} entrou(aram) no time" if changed else "já estava no melhor")
            )
        return "📋 Escalações refeitas (cada conta com o próprio elenco):\n" + "\n".join(lines)

    def _group(self, context: commands.Context) -> dict[str, Any] | None:
        register_user(context.author.id, context.author.display_name, self.database_path)
        return club_groups.group_of(context.author.id, self.database_path)

    async def _reply(self, context: commands.Context, text: str) -> None:
        await context.reply(text, mention_author=False)

    async def _owner_group(self, context: commands.Context) -> dict[str, Any] | None:
        group = self._group(context)
        if group is None:
            await self._reply(
                context, "Você não está em grupo nenhum. Crie um com `mm!grupo criar <nome>`."
            )
            return None
        if group["owner_user_id"] != str(context.author.id):
            await self._reply(context, "Só o dono do grupo pode fazer isso.")
            return None
        return group

    def _target(
        self, context: commands.Context, group: dict[str, Any], words: list[str]
    ) -> tuple[dict[str, Any] | None, list[str]]:
        """A conta citada (menção ou posição na ordem) e as palavras que sobraram."""
        mention = next((m for m in context.message.mentions if not m.bot), None)
        members = _member_by_id(group)
        rest = [w for w in words if not (w.startswith("<@") and w.endswith(">"))]
        if mention is not None:
            return members.get(str(mention.id)), rest
        by_rank = {m["rank"]: m for m in group["members"]}
        for index in (0, len(rest) - 1):
            if rest and club_groups.parse_rank(rest[index]) in by_rank:
                member = by_rank[club_groups.parse_rank(rest[index])]
                return member, rest[:index] + rest[index + 1 :]
        return None, rest

    # Comandos -------------------------------------------------------------------------------

    @commands.group(
        name="grupo", aliases=["grupos", "holding", "parceria"], invoke_without_command=True
    )
    async def group(self, context: commands.Context) -> None:
        """Painel do grupo de clubes (contas do mesmo dono)."""
        group = self._group(context)
        if group is None:
            invite = club_groups.pending_invite(context.author.id, self.database_path)
            text = (
                "🤝 **Grupo de clubes**: junte as suas contas (cada uma com o seu clube) e "
                "gerencie todas por uma só: o melhor XI na Principal, o resto nas outras, "
                "venda em massa e transferências entre elas.\n"
                "`mm!grupo criar <nome>` e depois `mm!grupo convidar @conta` (a conta aceita "
                "com `mm!grupo aceitar`)."
            )
            if invite:
                text += (
                    f"\n\n📨 Você tem convite do grupo **{invite['name']}**: "
                    "`mm!grupo aceitar` ou `mm!grupo recusar`."
                )
            await self._reply(context, text)
            return
        async with context.typing():
            embed = await self.overview(group)
        view = GroupPanel(self, context.author, group)
        view.message = await context.reply(embed=embed, view=view, mention_author=False)

    @group.command(name="criar", aliases=["novo", "fundar"])
    async def create(self, context: commands.Context, *, name: str = "") -> None:
        self._group(context)
        try:
            group = club_groups.create_group(context.author.id, name, self.database_path)
        except GroupError as error:
            await self._reply(context, str(error))
            return
        await self._reply(
            context,
            f"🤝 Grupo **{group['name']}** criado: esta conta é a **Principal**. Convide as "
            "outras com `mm!grupo convidar @conta` (cada uma aceita com `mm!grupo aceitar`).",
        )

    @group.command(name="convidar", aliases=["adicionar", "add"])
    async def invite(self, context: commands.Context, member: discord.Member) -> None:
        register_user(member.id, member.display_name, self.database_path)
        try:
            group = club_groups.invite(context.author.id, member.id, self.database_path)
        except GroupError as error:
            await self._reply(context, str(error))
            return
        await context.reply(
            f"📨 {member.mention}, você foi convidado para o grupo **{group['name']}**. "
            "Aceitando, o dono do grupo passa a gerenciar o seu elenco (transferir, escalar e "
            "vender); você continua com o seu clube e pode sair quando quiser.\n"
            "`mm!grupo aceitar` ou `mm!grupo recusar`.",
            mention_author=False,
        )

    @group.command(name="aceitar", aliases=["entrar"])
    async def accept(self, context: commands.Context) -> None:
        try:
            group = club_groups.answer_invite(context.author.id, True, self.database_path)
        except GroupError as error:
            await self._reply(context, str(error))
            return
        me = _member_by_id(group)[str(context.author.id)]
        await self._reply(
            context,
            f"✅ Você entrou no grupo **{group['name']}** como **{rank_label(me['rank'])}**. "
            "Para sair: `mm!grupo sair`.",
        )

    @group.command(name="recusar")
    async def decline(self, context: commands.Context) -> None:
        try:
            group = club_groups.answer_invite(context.author.id, False, self.database_path)
        except GroupError as error:
            await self._reply(context, str(error))
            return
        await self._reply(context, f"Convite do grupo **{group['name']}** recusado.")

    @group.command(name="sair")
    async def leave(self, context: commands.Context) -> None:
        try:
            group = club_groups.leave(context.author.id, self.database_path)
        except GroupError as error:
            await self._reply(context, str(error))
            return
        await self._reply(context, f"Você saiu do grupo **{group['name']}**.")

    @group.command(name="remover", aliases=["expulsar", "tirar"])
    async def remove(self, context: commands.Context, member: discord.Member) -> None:
        try:
            club_groups.remove_member(context.author.id, member.id, self.database_path)
        except GroupError as error:
            await self._reply(context, str(error))
            return
        await self._reply(context, f"{member.display_name} saiu do grupo.")

    @group.command(name="desfazer", aliases=["apagar", "encerrar"])
    async def disband(self, context: commands.Context, confirm: str | None = None) -> None:
        if (confirm or "").casefold() != "confirmar":
            await self._reply(
                context,
                "Isso acaba com o grupo (os clubes e elencos ficam como estão). Para seguir: "
                "`mm!grupo desfazer confirmar`.",
            )
            return
        try:
            club_groups.disband(context.author.id, self.database_path)
        except GroupError as error:
            await self._reply(context, str(error))
            return
        await self._reply(context, "Grupo desfeito. Cada conta segue com o seu clube.")

    @group.command(name="nome", aliases=["renomear"])
    async def rename(self, context: commands.Context, *, name: str = "") -> None:
        try:
            club_groups.rename(context.author.id, name, self.database_path)
        except GroupError as error:
            await self._reply(context, str(error))
            return
        await self._reply(context, f"Nome do grupo: **{' '.join(name.split())[:32]}**.")

    @group.command(name="ordem", aliases=["ordenar", "prioridade"])
    async def order(self, context: commands.Context) -> None:
        mentions = [m.id for m in context.message.mentions if not m.bot]
        if not mentions:
            await self._reply(
                context,
                "Cite as contas na ordem: `mm!grupo ordem @principal @secundaria @terciaria`.",
            )
            return
        try:
            group = club_groups.set_order(context.author.id, mentions, self.database_path)
        except GroupError as error:
            await self._reply(context, str(error))
            return
        await self._reply(
            context,
            "Nova ordem:\n"
            + "\n".join(f"{m['rank']}. {account_name(m)}" for m in group["members"]),
        )

    @group.command(name="otimizar", aliases=["organizar", "distribuir"])
    async def optimize(self, context: commands.Context) -> None:
        group = await self._owner_group(context)
        if group is None:
            return
        async with context.typing():
            embed, view = await self.plan_message(context.author.id, group)
        view.message = await context.reply(embed=embed, view=view, mention_author=False)

    @group.command(name="escalar", aliases=["escalacoes", "escalações"])
    async def lineups(self, context: commands.Context) -> None:
        group = await self._owner_group(context)
        if group is None:
            return
        await self._reply(context, await self.fill_all(group))

    @group.command(name="elenco", aliases=["jogadores", "todos"])
    async def roster(self, context: commands.Context, position: str | None = None) -> None:
        group = self._group(context)
        if group is None:
            await self._reply(context, "Você não está em grupo nenhum.")
            return
        players = club_groups.group_players(group, self.database_path)
        if position:
            players = [p for p in players if (p["game_position"] or "") == position.upper()]
        members = _member_by_id(group)
        lines = [
            f"**{p['overall']}** {p['game_position']} · {p['name']} · "
            f"{_short(members.get(p['owner_id']))}{' ⭐' if p['slot_order'] else ''}"
            f"{' 🔒' if p['locked'] else ''}"
            for p in players[:ROSTER_LINES]
        ]
        if len(players) > ROSTER_LINES:
            lines.append(f"… e mais {len(players) - ROSTER_LINES}")
        embed = discord.Embed(
            title=f"👥 Elenco do grupo {group['name']}"
            + (f" · {position.upper()}" if position else ""),
            description="\n".join(lines) or "Ninguém.",
            color=COLOR,
        )
        embed.set_footer(
            text=f"{len(players)} jogadores · ⭐ titular na conta · mm!grupo elenco CA"
        )
        await context.reply(embed=embed, mention_author=False)

    @group.command(name="mover", aliases=["transferir", "passar"])
    async def move(self, context: commands.Context, *, arguments: str = "") -> None:
        group = await self._owner_group(context)
        if group is None:
            return
        target, rest = self._target(context, group, arguments.split())
        query = " ".join(rest)
        if target is None or not query:
            await self._reply(
                context,
                "Use `mm!grupo mover <jogador> <conta>`: a conta é `@conta` ou a posição "
                "(`1`, `2`, `principal`, `secundaria`…).",
            )
            return
        players = club_groups.group_players(group, self.database_path)

        async def chosen(send, player: dict[str, Any]) -> None:
            try:
                moved = await asyncio.to_thread(
                    club_groups.move_player,
                    context.author.id,
                    group,
                    int(player["id"]),
                    target["discord_user_id"],
                    self.database_path,
                )
            except GroupError as error:
                await send(content=str(error), view=None)
                return
            equipped = await asyncio.to_thread(
                equip_if_better, target["discord_user_id"], int(player["id"]), self.database_path
            )
            source = _member_by_id(group).get(moved["owner_id"])
            text = (
                f"🔁 **{player_label(moved)}** foi da {_short(source)} para a "
                f"**{account_name(target)}**."
            )
            if equipped:
                text += f" Já entrou no time como **{equipped['position']}**."
            await send(content=text, view=None)

        player, candidates = resolve_player(query, players)
        if player is not None:
            await chosen(lambda **kwargs: context.reply(mention_author=False, **kwargs), player)
            return
        if not candidates:
            await self._reply(context, f"Não achei **{query[:60]}** em nenhuma conta do grupo.")
            return

        async def picked(interaction: discord.Interaction, player: dict[str, Any]) -> None:
            await chosen(interaction.response.edit_message, player)

        await context.reply(
            ambiguity_text(query, candidates),
            view=PlayerChoiceView(context.author.id, candidates, picked),
            mention_author=False,
        )

    @group.command(name="vender", aliases=["venda", "vendas"])
    async def sell(self, context: commands.Context, *, arguments: str = "") -> None:
        """`mm!grupo vender 2` abre o painel da conta; `mm!grupo vender abaixo 70` vende os
        reservas do filtro em todas as contas (ou só na citada)."""
        group = await self._owner_group(context)
        if group is None:
            return
        target, rest = self._target(context, group, arguments.split())
        filters = quick_filter(" ".join(rest))
        if rest and filters is None:
            await self._reply(
                context,
                "Filtros: `base`, `comum`… (raridade), `abaixo 70` ou `posicao ZAG`. Ex.: "
                "`mm!grupo vender abaixo 70` ou `mm!grupo vender 2 base`.",
            )
            return
        if filters is None:
            if target is None:
                view = OwnerView(context.author.id)
                view.add_item(AccountSaleSelect(group, self.database_path))
                view.message = await context.reply(
                    "De qual conta? (ou use um filtro: `mm!grupo vender abaixo 70`)",
                    view=view,
                    mention_author=False,
                )
                return
            view = SalePanelView(
                context.author.id,
                self.database_path,
                account_id=target["discord_user_id"],
                account_label=account_name(target),
            )
            view.message = await context.reply(embed=view.embed(), view=view, mention_author=False)
            return
        await self._group_quick_sale(context, group, target, filters, " ".join(rest))

    async def _group_quick_sale(self, context, group, target, filters, text) -> None:
        plan = await asyncio.to_thread(club_groups.optimize_plan, group, self.database_path)
        # Quem seria titular em alguma conta depois de otimizar não vai para a venda.
        keep = {pid for account in plan.accounts for pid, _ in account.lineup.values()}
        accounts = (
            [target["discord_user_id"]]
            if target
            else [m["discord_user_id"] for m in group["members"]]
        )
        try:
            found = club_groups.group_sale_ids(
                group, accounts, self.database_path, keep=keep, **filters
            )
        except TransactionError as error:
            await self._reply(context, str(error))
            return
        if not found:
            await self._reply(
                context,
                f"Nenhum reserva para vender com **{text}** (titulares, 🔒 e quem seria titular "
                "em outra conta ficam de fora).",
            )
            return
        members = _member_by_id(group)
        quotes = {user: sale_quote(user, ids, self.database_path) for user, ids in found.items()}
        total = sum(q["total"] for q in quotes.values())
        count = sum(len(ids) for ids in found.values())
        embed = discord.Embed(
            title=f"💰 Vender {count} jogador(es) do grupo · {text}",
            color=discord.Color.orange(),
        )
        for user, quote in quotes.items():
            names = ", ".join(player_label(p) for p in quote["players"][:8])
            more = len(quote["players"]) - 8
            embed.add_field(
                name=f"{account_name(members[user])}: {len(quote['players'])} · "
                f"{icons.memilets(quote['total'])}"[:256],
                value=(names + (f" e mais {more}" if more > 0 else ""))[:1024],
                inline=False,
            )
        embed.set_footer(
            text="Só reservas sem 🔒; quem seria titular em outra conta fica. Cada conta recebe "
            "o valor dos seus. A venda não pode ser desfeita."
        )

        async def done(interaction: discord.Interaction) -> None:
            lines = []
            for user, ids in found.items():
                try:
                    received = await asyncio.to_thread(sell_players, user, ids, self.database_path)
                except TransactionError as error:
                    lines.append(f"{account_name(members[user])}: {error}")
                    continue
                lines.append(
                    f"{account_name(members[user])}: {len(ids)} vendido(s), "
                    f"+{icons.memilets(received)}"
                )
            await interaction.response.edit_message(
                content="✅ Venda do grupo concluída.\n" + "\n".join(lines), embed=None, view=None
            )

        view = ConfirmView(
            context.author.id,
            done,
            confirm_label=f"Vender {count} por {total:,}".replace(",", "."),
            cancel_text="Venda cancelada.",
            danger=True,
        )
        view.message = await context.reply(embed=embed, view=view, mention_author=False)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(GroupsCog(bot))
