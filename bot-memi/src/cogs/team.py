"""Comandos de time, elenco e escalação."""

from __future__ import annotations

import asyncio

import discord
from discord.ext import commands

from src.db import DATABASE_PATH, normalize_player_name
from src.game import list_owned_players
from src.lineup_image import render_lineup
from src.teams import (
    FORMATIONS,
    STYLE_LABELS,
    TeamError,
    auto_fill_lineup,
    clear_lineup_slot,
    ensure_team,
    get_lineup,
    get_team,
    get_team_statistics,
    remove_player_from_lineup,
    scale_player,
    set_formation,
    set_lineup_slot,
    set_team_field,
    set_team_role,
    set_team_style,
)

TEAM_FIELDS = {
    "nome": "team_name",
    "cor": "color",
    "mascote": "mascot",
    "tecnico": "coach_name",
    "estadio": "stadium_name",
    "logo": "logo_path",
    "camisa": "kit_path",
}


POSITION_NAMES = {
    "GOL": "Goleiro",
    "ZAG": "Zagueiro",
    "LD": "Lateral direito",
    "LE": "Lateral esquerdo",
    "MC": "Volante / meio-campo",
    "MEI": "Meia",
    "PE": "Ponta esquerda",
    "PD": "Ponta direita",
    "CA": "Centroavante",
}


async def lineup_file(team: dict, lineup: list[dict]) -> discord.File:
    image = await asyncio.to_thread(render_lineup, team, lineup)
    return discord.File(image, filename="escalacao.png")


class LineupBuilderView(discord.ui.View):
    """Monta o time: escolhe a formação, clica na vaga e escolhe o jogador da posição."""

    def __init__(self, owner: discord.abc.User, database_path):
        super().__init__(timeout=600)
        self.owner = owner
        self.database_path = database_path
        self.selected_slot: int | None = None
        self.notice = ""
        self.message: discord.Message | None = None
        self._build()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner.id:
            await interaction.response.send_message(
                "Só o dono do time pode mexer nessa escalação. Use `mm!escalar` para abrir a sua.",
                ephemeral=True,
            )
            return False
        return True

    def _state(self) -> tuple[dict, list[dict]]:
        team = get_team(self.owner.id, self.database_path)
        return team, get_lineup(self.owner.id, self.database_path)

    def _build(self) -> None:
        self.clear_items()
        team, lineup = self._state()
        positions = FORMATIONS[team["formation"]]
        by_slot = {int(player["slot_order"]): player for player in lineup}

        formation = discord.ui.Select(
            placeholder=f"Formação: {team['formation']}",
            options=[
                discord.SelectOption(label=name, value=name, default=name == team["formation"])
                for name in FORMATIONS
            ],
            row=0,
        )
        formation.callback = self._on_formation
        self.add_item(formation)

        slot_options = []
        for slot, position in enumerate(positions, start=1):
            player = by_slot.get(slot)
            label = (
                f"{position} · {player['name']} ({player['effective_overall']})"
                if player
                else f"{position} · vaga livre"
            )
            slot_options.append(
                discord.SelectOption(
                    label=label[:100],
                    value=str(slot),
                    description=POSITION_NAMES[position],
                    emoji="✅" if player else "➕",
                    default=slot == self.selected_slot,
                )
            )
        slots = discord.ui.Select(placeholder="Escolha a posição", options=slot_options, row=1)
        slots.callback = self._on_slot
        self.add_item(slots)

        if self.selected_slot is not None:
            position = positions[self.selected_slot - 1]
            current = by_slot.get(self.selected_slot)
            elsewhere = {
                int(player["id"]): int(player["slot_order"]) for player in lineup
            }
            players = list_owned_players(
                self.owner.id,
                self.database_path,
                limit=24,
                position=position,
                sort_by="overall",
            )
            options = []
            for player in players:
                description = player["club_name"] or "Passe livre"
                if int(player["id"]) in elsewhere and int(player["id"]) != (current or {}).get("id"):
                    description = "já escalado em outra vaga (muda de lugar)"
                options.append(
                    discord.SelectOption(
                        label=f"{player['overall']} · {player['name']}"[:100],
                        value=str(player["id"]),
                        description=description[:100],
                        default=current is not None and int(current["id"]) == int(player["id"]),
                    )
                )
            if current is not None:
                options.append(
                    discord.SelectOption(label="Deixar a vaga vazia", value="clear", emoji="🗑️")
                )
            if options:
                picker = discord.ui.Select(
                    placeholder=f"Jogador para {position} ({POSITION_NAMES[position]})",
                    options=options,
                    row=2,
                )
                picker.callback = self._on_player
            else:
                picker = discord.ui.Select(
                    placeholder=f"Você não tem nenhum {position} no elenco",
                    options=[discord.SelectOption(label="—", value="none")],
                    disabled=True,
                    row=2,
                )
            self.add_item(picker)

        auto = discord.ui.Button(label="Escalar automático", emoji="🪄", style=discord.ButtonStyle.primary, row=3)
        auto.callback = self._on_auto
        self.add_item(auto)
        done = discord.ui.Button(label="Concluir", emoji="✅", style=discord.ButtonStyle.success, row=3)
        done.callback = self._on_done
        self.add_item(done)

    def embed(self, team: dict, lineup: list[dict]) -> discord.Embed:
        complete = len(lineup) == 11
        embed = discord.Embed(
            title=f"✏️ Escalação · {team['team_name']}",
            description=(
                (f"{self.notice}\n\n" if self.notice else "")
                + "1. Escolha a **formação**\n"
                "2. Clique na **posição**\n"
                "3. Escolha o **jogador** (ordenados por overall)\n"
                + ("\n✅ Time completo! Pronto para `mm!amistoso`." if complete
                   else f"\n{len(lineup)}/11 titulares escalados.")
            ),
            color=discord.Color.from_str(team["color"]),
        )
        embed.set_image(url="attachment://escalacao.png")
        return embed

    async def refresh(self, interaction: discord.Interaction) -> None:
        self._build()
        team, lineup = self._state()
        await interaction.response.defer()
        file = await lineup_file(team, lineup)
        await interaction.edit_original_response(
            embed=self.embed(team, lineup), attachments=[file], view=self
        )

    async def _on_formation(self, interaction: discord.Interaction) -> None:
        value = interaction.data["values"][0]
        dropped = set_formation(
            self.owner.id, value, self.database_path, drop_incompatible=True
        )
        self.selected_slot = None
        self.notice = f"Formação **{value}**." + (
            f" {dropped} titular(es) sem vaga saíram." if dropped else ""
        )
        await self.refresh(interaction)

    async def _on_slot(self, interaction: discord.Interaction) -> None:
        self.selected_slot = int(interaction.data["values"][0])
        self.notice = ""
        await self.refresh(interaction)

    async def _on_player(self, interaction: discord.Interaction) -> None:
        value = interaction.data["values"][0]
        try:
            if value == "clear":
                clear_lineup_slot(self.owner.id, self.selected_slot, self.database_path)
                self.notice = "Vaga liberada."
            else:
                player = set_lineup_slot(
                    self.owner.id, self.selected_slot, int(value), self.database_path
                )
                self.notice = f"**{player['name']}** escalado."
                self.selected_slot = None
        except TeamError as error:
            self.notice = f"⚠️ {error}"
        await self.refresh(interaction)

    async def _on_auto(self, interaction: discord.Interaction) -> None:
        filled = auto_fill_lineup(self.owner.id, self.database_path)
        self.notice = (
            f"🪄 {filled} vaga(s) preenchidas com seus maiores overalls."
            if filled
            else "Não há jogadores livres das posições que faltam."
        )
        self.selected_slot = None
        await self.refresh(interaction)

    async def _on_done(self, interaction: discord.Interaction) -> None:
        team, lineup = self._state()
        self.stop()
        embed = self.embed(team, lineup)
        embed.title = f"📋 {team['team_name']}"
        embed.description = f"{team['formation']} · {len(lineup)}/11 titulares"
        await interaction.response.edit_message(embed=embed, view=None)

    async def on_timeout(self) -> None:
        if self.message:
            try:
                await self.message.edit(view=None)
            except discord.HTTPException:
                pass


class TeamProfileView(discord.ui.View):
    def __init__(self, owner: discord.abc.User, database_path):
        super().__init__(timeout=300)
        self.owner = owner
        self.database_path = database_path

    @discord.ui.button(label="Editar escalação", emoji="✏️", style=discord.ButtonStyle.primary)
    async def edit(self, interaction: discord.Interaction, button) -> None:
        if interaction.user.id != self.owner.id:
            await interaction.response.send_message(
                "Use `mm!escalar` para editar o seu time.", ephemeral=True
            )
            return
        builder = LineupBuilderView(self.owner, self.database_path)
        builder.message = interaction.message
        await builder.refresh(interaction)


class TeamCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.database_path = getattr(bot, "database_path", DATABASE_PATH)

    @commands.command(name="time")
    async def team(
        self,
        context: commands.Context,
        field: str | None = None,
        *,
        value: str | None = None,
    ) -> None:
        ensure_team(context.author.id, context.author.display_name, self.database_path)
        if field:
            key = TEAM_FIELDS.get(field.casefold())
            if key is None or value is None:
                await context.reply(
                    "Use `mm!time nome|cor|mascote|tecnico|estadio|logo|camisa <valor>`.",
                    mention_author=False,
                )
                return
            try:
                set_team_field(
                    context.author.id,
                    key,
                    value,
                    self.database_path,
                )
            except TeamError as error:
                await context.reply(str(error), mention_author=False)
                return
        team = get_team(context.author.id, self.database_path)
        embed = discord.Embed(
            title=team["team_name"],
            color=discord.Color.from_str(team["color"]),
        )
        embed.add_field(name="Formação", value=team["formation"], inline=True)
        embed.add_field(
            name="Estilo",
            value=STYLE_LABELS[team["style"]],
            inline=True,
        )
        embed.add_field(
            name="Mascote",
            value=team["mascot"] or "Não definido",
            inline=True,
        )
        embed.add_field(
            name="Técnico",
            value=team["coach_name"] or "Não definido",
            inline=True,
        )
        embed.add_field(
            name="Estádio",
            value=team["stadium_name"] or "Não definido",
            inline=True,
        )
        statistics = get_team_statistics(context.author.id, self.database_path)
        embed.add_field(
            name="Jogos / títulos",
            value=f"{statistics['matches']} jogos · {statistics['trophies']} títulos",
            inline=True,
        )
        if statistics["form"]:
            embed.add_field(
                name="Forma (últimos 5)",
                value=" · ".join(statistics["form"]),
                inline=True,
            )
        if statistics["top_scorer"]:
            name, goals = statistics["top_scorer"]
            embed.add_field(
                name="Artilheiro",
                value=f"{name} · {goals} gols",
                inline=True,
            )
        if statistics["most_appearances"]:
            name, appearances = statistics["most_appearances"]
            embed.add_field(
                name="Mais partidas",
                value=f"{name} · {appearances}",
                inline=True,
            )
        lineup = get_lineup(context.author.id, self.database_path)
        status = (
            "✅ Escalação completa."
            if len(lineup) == 11
            else f"Escalação: {len(lineup)}/11 titulares. Clique em **Editar escalação**."
        )
        embed.description = ("Perfil do time atualizado.\n" if field else "") + status
        file = await lineup_file(team, lineup)
        embed.set_image(url="attachment://escalacao.png")
        await context.reply(
            embed=embed,
            file=file,
            view=TeamProfileView(context.author, self.database_path),
            mention_author=False,
        )

    @commands.command(name="escalar", aliases=["escalacao", "lineup"])
    async def scale(
        self,
        context: commands.Context,
        *,
        query: str | None = None,
    ) -> None:
        ensure_team(context.author.id, context.author.display_name, self.database_path)
        if not query:
            builder = LineupBuilderView(context.author, self.database_path)
            team, lineup = builder._state()
            file = await lineup_file(team, lineup)
            builder.message = await context.reply(
                embed=builder.embed(team, lineup),
                file=file,
                view=builder,
                mention_author=False,
            )
            return
        parts = query.split(maxsplit=1)
        if parts[0].casefold() in {"remover", "tirar"}:
            if len(parts) != 2:
                await context.reply(
                    "Informe o ID do jogador a remover: `mm!escalar remover <id>`.",
                    mention_author=False,
                )
                return
            player_id = self._parse_player_id(parts[1])
            if player_id is None or not remove_player_from_lineup(
                context.author.id,
                player_id,
                self.database_path,
            ):
                await context.reply("Esse jogador não está escalado.", mention_author=False)
                return
            await context.reply("Jogador removido da escalação.", mention_author=False)
            return

        player_id = self._parse_player_id(query)
        if player_id is None:
            owned = list_owned_players(
                context.author.id,
                self.database_path,
                limit=1000,
            )
            normalized_query = normalize_player_name(query)
            matches = [
                player
                for player in owned
                if normalized_query in normalize_player_name(player["name"])
            ]
            if len(matches) != 1:
                await context.reply(
                    "Informe um ID de jogador do seu elenco, ou refine o nome.",
                    mention_author=False,
                )
                return
            player_id = int(matches[0]["id"])
        try:
            slot = scale_player(
                context.author.id,
                player_id,
                self.database_path,
            )
        except TeamError as error:
            await context.reply(str(error), mention_author=False)
            return
        await context.reply(
            f"Jogador escalado na posição {slot}. Consulte `mm!time` para ver a escalação.",
            mention_author=False,
        )

    @commands.command(name="tatica")
    async def tactics(
        self,
        context: commands.Context,
        operation: str | None = None,
        *,
        value: str | None = None,
    ) -> None:
        ensure_team(context.author.id, context.author.display_name, self.database_path)
        if operation and value:
            op = normalize_player_name(operation)
            try:
                if op in {"formacao", "formação"}:
                    set_formation(context.author.id, value, self.database_path)
                elif op in {"estilo", "style"}:
                    set_team_style(context.author.id, value, self.database_path)
                elif op in {"capitao", "capitão", "falta", "penalti", "pênalti"}:
                    role = {
                        "capitão": "capitao",
                        "pênalti": "penalti",
                    }.get(op, op)
                    player_id = self._parse_player_id(value)
                    if player_id is None:
                        raise TeamError("Informe o ID do jogador escalado.")
                    set_team_role(
                        context.author.id,
                        role,
                        player_id,
                        self.database_path,
                    )
                else:
                    raise TeamError(
                        "Use formacao, estilo, capitao, falta ou penalti."
                    )
            except TeamError as error:
                await context.reply(str(error), mention_author=False)
                return
        team = get_team(context.author.id, self.database_path)
        lineup = get_lineup(context.author.id, self.database_path)
        lines = [
            f"**Formação:** {team['formation']} "
            f"(`mm!tatica formacao <{'|'.join(FORMATIONS)}>`) ",
            f"**Estilo:** {STYLE_LABELS[team['style']]} "
            "(`equilibrado`, `ataque`, `contra-ataque`)",
            f"**Titulares:** {len(lineup)}/11",
            "**Capitão:** cosmético; **falta/pênalti:** definidos por ID "
            "com `mm!tatica capitao|falta|penalti <id>`.",
        ]
        await context.reply("\n".join(lines), mention_author=False)

    @staticmethod
    def _parse_player_id(value: str) -> int | None:
        try:
            return int(value.strip())
        except ValueError:
            return None


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(TeamCog(bot))
