"""Comandos do clube: Centro de Treinamento, renda, bilheteria e cosméticos."""

from __future__ import annotations

import asyncio
from typing import Any

import discord
from discord.ext import commands

from src import icons
from src.boards_image import render_missions, render_season
from src.cosmetic_card import render_cosmetic_card
from src.missions import board, claim_rewards
from src.season import month_label, parse_month, season_table
from src.tower_image import render_tower
from src.cogs.common import ConfirmView, intro_embed
from src.cosmetics import (
    COSMETIC_KINDS,
    KIND_ALIASES,
    CosmeticError,
    equip,
    equipped_name,
    list_owned,
    unequip,
)
from src.db import DATABASE_PATH, normalize_player_name
from src.game import register_user
from src.income import (
    INCOME_BASE,
    INCOME_LOSS,
    INCOME_PER_GOAL,
    INCOME_WIN,
    WALKOUT_MARGIN,
    BOX_OFFICE_PER_GAME,
    BOX_OFFICE_PER_WIN,
    box_office_status,
    collect_box_office,
    income_status,
    venue_name,
)
from src.perks import TOWER_FLOORS
from src.teams import ensure_team, get_team
from src.tower import TowerError, build_floor, destroy_preview, destroy_tower, tower_status

TOWER_INTRO = (
    "O Centro de Treinamento é uma torre de 12 andares com upgrades eternos. Você constrói os "
    "andares com `mm!construir (1-12)`, em qualquer ordem, e cada andar custa mais que o "
    "anterior. Você pode destruir a torre com `mm!destruir` e recuperar todos os Memilets, "
    "perdendo apenas os upgrades."
)
INCOME_INTRO = (
    "A renda é o dinheiro que seu estádio rende no primeiro amistoso do dia jogando em casa. "
    "Gols e vitórias aumentam o ganho; derrotas e torcida insatisfeita diminuem. Abaixo você "
    "vê quanto seu time já arrecadou."
)
BOX_OFFICE_INTRO = (
    "A bilheteria é o caixa do andar 5 do Centro de Treinamento: cada jogo do campeonato "
    "diário guarda Memilets numa reserva, e a vitória guarda o dobro. O dinheiro fica "
    "guardado até o limite e você coleta quando quiser."
)
COSMETICS_INTRO = (
    "Cosméticos deixam o clube com a sua cara: emblema, uniforme, estádio, técnico, liga e "
    "mascote. Eles saem nas crates e só dá para equipar o que é seu. Cada um equipado (menos "
    "o estádio) soma +100 ao máximo do daily; o estádio libera a renda do amistoso."
)
MISSIONS_INTRO = (
    "Missões dão Memilets por coisas que você já faz no jogo: rolar, contratar, jogar e abrir "
    "crates. São 3 diárias, que viram à meia-noite, e 2 semanais, que viram na segunda. O "
    "progresso conta sozinho; quando a barra encher, resgate no botão."
)
SEASON_INTRO = (
    "A temporada soma todos os campeonatos diários do mês: 3 pontos por vitória e 1 por "
    "empate, com os títulos como primeiro desempate. Use `mm!temporada 9/2026` para ver um "
    "mês anterior."
)
REMOVE_WORDS = {"remover", "tirar", "nenhum", "nenhuma", "limpar"}


def _thousands(value: int) -> str:
    return f"{value:,}".replace(",", ".")


def _relative(moment) -> str:
    unix = int(moment.timestamp())
    return f"<t:{unix}:R> (<t:{unix}:t>)"


class CosmeticGalleryView(discord.ui.View):
    """Um cosmético por página, com a carta grande; ◀ ▶ navegam e Equipar equipa o item."""

    def __init__(self, cog: "ClubCog", owner, items: list[dict[str, Any]], intro: str):
        super().__init__(timeout=300)
        self.cog = cog
        self.owner = owner
        self.items = items
        self.intro = intro
        self.index = 0
        self.message: discord.Message | None = None
        self._sync()

    def _sync(self) -> None:
        self.previous.disabled = self.index == 0
        self.next.disabled = self.index >= len(self.items) - 1

    def _equipped(self, item: dict[str, Any]) -> bool:
        team = get_team(self.owner.id, self.cog.database_path)
        column = COSMETIC_KINDS[item["kind"]][2]
        name = equipped_name(item["kind"], team.get(column), self.cog.database_path)
        return name == item["name"]

    async def _page(self, notice: str = "") -> tuple[discord.Embed, discord.File]:
        item = self.items[self.index]
        equipped = self._equipped(item)
        self.equip.disabled = equipped
        self.equip.label = "Equipado" if equipped else "Equipar"
        image = await asyncio.to_thread(
            render_cosmetic_card, item, equipped=equipped, owner=self.owner.display_name
        )
        embed = discord.Embed(
            title=f"🎽 Cosméticos de {self.owner.display_name}",
            description=(notice + "\n\n" if notice else "") + self.intro,
            color=discord.Color.from_str(get_team(self.owner.id, self.cog.database_path)["color"]),
        )
        embed.set_image(url="attachment://cosmetico.png")
        embed.set_footer(text=f"{self.index + 1}/{len(self.items)} · mm!cosmeticos <tipo> filtra")
        return embed, discord.File(image, filename="cosmetico.png")

    async def send(self, send) -> None:
        embed, file = await self._page()
        self.message = await send(embed=embed, file=file, view=self)

    async def _show(self, interaction: discord.Interaction, notice: str = "") -> None:
        self._sync()
        await interaction.response.defer()
        embed, file = await self._page(notice)
        await interaction.edit_original_response(embed=embed, attachments=[file], view=self)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner.id:
            await interaction.response.send_message(
                "Use `mm!cosmeticos` para ver os seus.", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="◀", style=discord.ButtonStyle.secondary)
    async def previous(self, interaction: discord.Interaction, button) -> None:
        self.index -= 1
        await self._show(interaction)

    @discord.ui.button(label="▶", style=discord.ButtonStyle.secondary)
    async def next(self, interaction: discord.Interaction, button) -> None:
        self.index += 1
        await self._show(interaction)

    @discord.ui.button(label="Equipar", style=discord.ButtonStyle.success)
    async def equip(self, interaction: discord.Interaction, button) -> None:
        item = self.items[self.index]
        try:
            equip(self.owner.id, item["kind"], item["name"], self.cog.database_path)
        except CosmeticError as error:
            await interaction.response.send_message(str(error), ephemeral=True)
            return
        await self._show(interaction, f"✅ **{item['name']}** equipado.")

    @discord.ui.button(label="Lista", emoji="📋", style=discord.ButtonStyle.primary)
    async def show_list(self, interaction: discord.Interaction, button) -> None:
        lines = [f"{COSMETIC_KINDS[item['kind']][1]} {item['name']}" for item in self.items[:60]]
        await interaction.response.send_message("\n".join(lines)[:1900], ephemeral=True)

    async def on_timeout(self) -> None:
        if self.message:
            try:
                await self.message.edit(view=None)
            except discord.HTTPException:
                pass


class MissionsView(discord.ui.View):
    def __init__(self, cog: "ClubCog", owner):
        super().__init__(timeout=300)
        self.cog = cog
        self.owner = owner

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner.id:
            await interaction.response.send_message(
                "Use `mm!missoes` para ver as suas.", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="Resgatar prontas", emoji="🎁", style=discord.ButtonStyle.success)
    async def collect(self, interaction: discord.Interaction, button) -> None:
        total, count = claim_rewards(self.owner.id, self.cog.database_path)
        embed, file = await self.cog.missions_message(self.owner)
        notice = (
            f"🎁 {count} missão(ões) resgatada(s): +{icons.memilets(total)} Memilets."
            if count
            else "Nenhuma missão pronta para resgatar ainda."
        )
        embed.description = notice + "\n\n" + embed.description
        button.disabled = True
        await interaction.response.edit_message(embed=embed, attachments=[file], view=self)


class ClubCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.database_path = getattr(bot, "database_path", DATABASE_PATH)

    # Centro de Treinamento ---------------------------------------------------------------

    def tower_embed(self, user: discord.abc.User) -> discord.Embed:
        """Texto do menu da torre; o prédio vem na imagem (`tower_file`)."""
        status = tower_status(user.id, self.database_path)
        embed = intro_embed("🏗️ Centro de Treinamento", TOWER_INTRO, color=discord.Color.teal())
        effects = " · ".join(f"**{floor}** {effect}" for floor, effect in TOWER_FLOORS.items())
        embed.add_field(name="Andares", value=effects[:1024], inline=False)
        embed.add_field(
            name="Próximo andar", value=f"{icons.memilets(status.next_price)}", inline=True
        )
        embed.add_field(
            name="Investido", value=f"{icons.memilets(status.spent)} (volta inteiro)", inline=True
        )
        embed.set_image(url="attachment://torre.png")
        embed.set_footer(text="mm!construir <1-12> constrói · mm!destruir devolve tudo")
        return embed

    async def tower_file(self, user: discord.abc.User) -> discord.File:
        status = tower_status(user.id, self.database_path)
        team = get_team(user.id, self.database_path)
        image = await asyncio.to_thread(render_tower, status, team)
        return discord.File(image, filename="torre.png")

    @commands.command(name="construir", aliases=["torre", "ct"])
    async def build(self, context: commands.Context, floor: str | None = None) -> None:
        ensure_team(context.author.id, context.author.display_name, self.database_path)
        if floor is None:
            await context.reply(
                embed=self.tower_embed(context.author),
                file=await self.tower_file(context.author),
                mention_author=False,
            )
            return
        if not floor.isdigit() or int(floor) not in TOWER_FLOORS:
            await context.reply(
                "Escolha um andar de 1 a 12: `mm!construir 5`.", mention_author=False
            )
            return
        number = int(floor)
        status = tower_status(context.author.id, self.database_path)
        if status.built_in_current(number):
            await context.reply(
                f"O andar {number} já está pronto nesta construção. Termine os outros antes de "
                "começar a próxima.",
                mention_author=False,
            )
            return
        author = context.author

        async def confirm(interaction: discord.Interaction) -> None:
            try:
                result = build_floor(author.id, author.display_name, number, self.database_path)
            except TowerError as error:
                await interaction.response.edit_message(content=str(error), view=None)
                return
            text = (
                f"🏗️ Andar **{number}** construído por {icons.memilets(result.price)} Memilets: "
                f"{TOWER_FLOORS[number]}."
            )
            if result.ball_player:
                text += f"\n⚽ **{result.ball_player['name']}** ganhou 1 bola."
            await interaction.response.edit_message(
                content=text, view=None, attachments=[await self.tower_file(author)]
            )

        view = ConfirmView(
            author.id, confirm, confirm_label="Construir", cancel_text="Construção cancelada."
        )
        view.message = await context.reply(
            f"Construir o andar **{number}** ({TOWER_FLOORS[number]}) por "
            f"{icons.memilets(status.next_price)} Memilets?",
            view=view,
            mention_author=False,
        )

    @commands.command(name="destruir")
    async def destroy(self, context: commands.Context) -> None:
        preview = destroy_preview(context.author.id, self.database_path)
        if not preview.floors:
            await context.reply("Você ainda não construiu nenhum andar.", mention_author=False)
            return
        lost = [
            f"• Andar {floor}: {TOWER_FLOORS[floor]}" + (f" (×{times})" if times > 1 else "")
            for floor, times in sorted(preview.floors.items())
        ]
        if preview.ball_players:
            lost.append("• Bolas do andar 12: " + ", ".join(preview.ball_players))
        author = context.author

        async def confirm(interaction: discord.Interaction) -> None:
            try:
                result = destroy_tower(author.id, self.database_path)
            except TowerError as error:
                await interaction.response.edit_message(content=str(error), embed=None, view=None)
                return
            await interaction.response.edit_message(
                content=(
                    f"💥 Torre destruída. Voltaram {icons.memilets(result.refund)} Memilets "
                    "para o seu saldo."
                ),
                embed=None,
                view=None,
            )

        embed = discord.Embed(
            title="💥 Destruir o Centro de Treinamento?",
            description=(
                f"Você recebe de volta {icons.memilets(preview.refund)} Memilets, sem perda.\n"
                "**Vai perder todos estes upgrades:**\n" + "\n".join(lost)
            )[:4000],
            color=discord.Color.red(),
        )
        view = ConfirmView(
            author.id,
            confirm,
            confirm_label="Destruir a torre",
            cancel_text="A torre continua de pé.",
            danger=True,
        )
        view.message = await context.reply(embed=embed, view=view, mention_author=False)

    # Renda e bilheteria -------------------------------------------------------------------

    @commands.command(name="renda")
    async def income(self, context: commands.Context) -> None:
        ensure_team(context.author.id, context.author.display_name, self.database_path)
        status = income_status(context.author.id, self.database_path)
        team = get_team(context.author.id, self.database_path)
        embed = intro_embed("🏟️ Renda do amistoso", INCOME_INTRO, color=discord.Color.green())
        rules = [
            f"• Base: **{INCOME_BASE}**" + (f" (+{status.bonus} de bônus)" if status.bonus else ""),
            f"• Cada gol do seu time: **+{INCOME_PER_GOAL}**",
            f"• Vitória: **+{INCOME_WIN}** · derrota: **{INCOME_LOSS}**",
            f"• Perder por {WALKOUT_MARGIN}+ gols sem marcar: a torcida vai embora e a renda zera",
            "• Só o **primeiro amistoso do dia** em casa (`mm!amistoso @pessoa`) gera renda; o "
            "mandante confirma antes. Os outros amistosos são livres.",
            "• O intervalo é de 24 h e nenhum upgrade o reduz.",
        ]
        embed.description += "\n\n**Regras**\n" + "\n".join(rules)
        embed.add_field(name="Estádio", value=venue_name(team), inline=False)
        if not status.stadium:
            availability = "Sem estádio equipado: o amistoso não gera renda. Ganhe um nas crates."
        elif status.available_at:
            availability = f"Próxima renda {_relative(status.available_at)}"
        else:
            availability = "✅ A renda de hoje está disponível."
        embed.add_field(name="Disponível", value=availability, inline=False)
        embed.add_field(
            name="Total arrecadado", value=f"{icons.memilets(status.total)} Memilets", inline=False
        )
        await context.reply(embed=embed, mention_author=False)

    @commands.command(name="bilheteria")
    async def box_office(self, context: commands.Context) -> None:
        register_user(context.author.id, context.author.display_name, self.database_path)
        collected = collect_box_office(context.author.id, self.database_path)
        status = box_office_status(context.author.id, self.database_path)
        embed = intro_embed("🎟️ Bilheteria", BOX_OFFICE_INTRO, color=discord.Color.dark_gold())
        embed.description += (
            f"\n\n• {BOX_OFFICE_PER_GAME} por jogo e {BOX_OFFICE_PER_WIN} por vitória "
            "no campeonato diário\n"
            f"• Limite da reserva: **{_thousands(status.cap)}** (sobe com `mm!upgrade bilheteria`)"
        )
        if not status.active:
            embed.add_field(
                name="Ainda fechada",
                value="Construa o andar 5 com `mm!construir 5` para abrir a bilheteria.",
                inline=False,
            )
        if collected:
            embed.add_field(
                name="Coletado agora", value=f"{icons.memilets(collected)} Memilets", inline=True
            )
        embed.add_field(
            name="Reserva",
            value=f"{_thousands(status.reserve)}/{_thousands(status.cap)}",
            inline=True,
        )
        embed.add_field(
            name="Total coletado", value=f"{icons.memilets(status.total)} Memilets", inline=True
        )
        await context.reply(embed=embed, mention_author=False)

    # Missões e temporada ------------------------------------------------------------------

    async def missions_message(self, user) -> tuple[discord.Embed, discord.File]:
        rows = board(user.id, self.database_path)
        image = await asyncio.to_thread(render_missions, rows, user.display_name)
        ready = sum(1 for row in rows if row["progress"] >= row["goal"] and not row["claimed"])
        embed = intro_embed(
            "🎯 Missões", MISSIONS_INTRO, color=discord.Color.from_rgb(63, 198, 216)
        )
        if ready:
            embed.description += f"\n\n**{ready} pronta(s) para resgatar.**"
        embed.set_image(url="attachment://missoes.png")
        return embed, discord.File(image, filename="missoes.png")

    @commands.command(name="missoes", aliases=["missões", "missao", "missão", "quests"])
    async def missions(self, context: commands.Context, action: str | None = None) -> None:
        register_user(context.author.id, context.author.display_name, self.database_path)
        notice = ""
        if action and normalize_player_name(action) in {"resgatar", "coletar", "claim"}:
            total, count = claim_rewards(context.author.id, self.database_path)
            notice = (
                f"🎁 {count} missão(ões) resgatada(s): +{icons.memilets(total)} Memilets.\n\n"
                if count
                else "Nenhuma missão pronta para resgatar ainda.\n\n"
            )
        embed, file = await self.missions_message(context.author)
        embed.description = notice + embed.description
        await context.reply(
            embed=embed, file=file, view=MissionsView(self, context.author), mention_author=False
        )

    @commands.command(name="temporada", aliases=["season"])
    async def season(self, context: commands.Context, *, month: str | None = None) -> None:
        if context.guild is None:
            await context.reply("A temporada é de cada servidor.", mention_author=False)
            return
        try:
            chosen = parse_month(month)
        except ValueError as error:
            await context.reply(str(error), mention_author=False)
            return
        table = season_table(context.guild.id, chosen, self.database_path)
        title = month_label(chosen)
        image = await asyncio.to_thread(render_season, table, title)
        embed = intro_embed(f"🗓️ Temporada · {title}", SEASON_INTRO, color=discord.Color.gold())
        if table:
            leader = table[0]
            embed.description += (
                f"\n\nLíder: **{leader['team_name']}** com {leader['points']} pontos "
                f"e {leader['titles']} título(s)."
            )
        embed.set_image(url="attachment://temporada.png")
        await context.reply(
            embed=embed, file=discord.File(image, filename="temporada.png"), mention_author=False
        )

    # Cosméticos ---------------------------------------------------------------------------

    @commands.command(name="cosmeticos", aliases=["cosméticos", "inventario", "inventário", "inv"])
    async def cosmetics(self, context: commands.Context, kind: str | None = None) -> None:
        ensure_team(context.author.id, context.author.display_name, self.database_path)
        wanted = KIND_ALIASES.get(normalize_player_name(kind)) if kind else None
        if kind and wanted is None:
            await context.reply(
                "Tipos: emblema, uniforme, estadio, tecnico, liga, mascote.", mention_author=False
            )
            return
        team = get_team(context.author.id, self.database_path)
        owned = list_owned(context.author.id, wanted, self.database_path)
        embed = intro_embed(
            f"🎽 Cosméticos de {context.author.display_name}",
            COSMETICS_INTRO,
            color=discord.Color.from_str(team["color"]),
        )
        if owned:
            view = CosmeticGalleryView(self, context.author, owned, embed.description)
            await view.send(lambda **kwargs: context.reply(mention_author=False, **kwargs))
            return
        for key, (label, emoji, column, _) in COSMETIC_KINDS.items():
            if wanted and key != wanted:
                continue
            equipped = equipped_name(key, team.get(column), self.database_path)
            items = [item["name"] for item in owned if item["kind"] == key]
            lines = [
                f"{'✅ ' if equipped and name == equipped else '• '}{name}" for name in items[:15]
            ]
            if len(items) > 15:
                lines.append(f"… e mais {len(items) - 15} (`mm!cosmeticos {key}`)")
            embed.add_field(
                name=f"{emoji} {label} ({len(items)})",
                value=("\n".join(lines) or "Nenhum ainda")[:1024],
                inline=True,
            )
        embed.set_footer(
            text="Equipe com mm!emblema, mm!uniforme, mm!estadio, mm!tecnico, mm!liga, mm!mascote"
        )
        await context.reply(embed=embed, mention_author=False)

    async def _equip(self, context: commands.Context, kind: str, query: str | None) -> None:
        ensure_team(context.author.id, context.author.display_name, self.database_path)
        label, emoji, column, _ = COSMETIC_KINDS[kind]
        if not query:
            team = get_team(context.author.id, self.database_path)
            equipped = equipped_name(kind, team.get(column), self.database_path)
            owned = [
                item["name"] for item in list_owned(context.author.id, kind, self.database_path)
            ]
            text = f"{emoji} {label} equipado: **{equipped or 'nenhum'}**\n"
            if owned:
                text += "Seus: " + ", ".join(owned[:25]) + (" …" if len(owned) > 25 else "")
                text += f"\nEquipe com `mm!{kind} <nome>` ou tire com `mm!{kind} remover`."
            else:
                text += "Você ainda não tem nenhum. Eles saem nas crates (`mm!crates`)."
            await context.reply(text, mention_author=False)
            return
        if normalize_player_name(query) in REMOVE_WORDS:
            unequip(context.author.id, kind, self.database_path)
            await context.reply(f"{emoji} {label} removido do time.", mention_author=False)
            return
        try:
            item = equip(context.author.id, kind, query, self.database_path)
        except CosmeticError as error:
            await context.reply(str(error), mention_author=False)
            return
        extra = (
            " O amistoso em casa agora pode gerar renda (`mm!renda`)."
            if kind == "estadio"
            else " +100 no máximo do daily."
        )
        await context.reply(
            f"{emoji} {label} equipado: **{item['name']}**.{extra}", mention_author=False
        )

    @commands.command(name="emblema", aliases=["emblemas"])
    async def emblem(self, context: commands.Context, *, query: str | None = None) -> None:
        await self._equip(context, "emblema", query)

    @commands.command(name="uniforme", aliases=["camisa"])
    async def kit(self, context: commands.Context, *, query: str | None = None) -> None:
        await self._equip(context, "uniforme", query)

    @commands.command(name="estadio", aliases=["estádio"])
    async def stadium(self, context: commands.Context, *, query: str | None = None) -> None:
        await self._equip(context, "estadio", query)

    @commands.command(name="tecnico", aliases=["técnico", "treinador"])
    async def coach(self, context: commands.Context, *, query: str | None = None) -> None:
        await self._equip(context, "tecnico", query)

    @commands.command(name="liga")
    async def league(self, context: commands.Context, *, query: str | None = None) -> None:
        await self._equip(context, "liga", query)

    @commands.command(name="mascote")
    async def mascot(self, context: commands.Context, *, query: str | None = None) -> None:
        await self._equip(context, "mascote", query)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ClubCog(bot))
