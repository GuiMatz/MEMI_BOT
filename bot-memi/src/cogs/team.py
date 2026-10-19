"""Comandos de time, elenco e escalação."""

from __future__ import annotations

import discord
from discord.ext import commands

from src.db import DATABASE_PATH
from src.game import list_owned_players
from src.lineup_image import render_lineup
from src.teams import (
    FORMATIONS,
    STYLE_LABELS,
    TeamError,
    ensure_team,
    get_lineup,
    get_team,
    get_team_statistics,
    get_valid_lineup,
    remove_player_from_lineup,
    scale_player,
    set_formation,
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
        if field:
            embed.description = "Perfil do time atualizado."
        try:
            valid_team, lineup = get_valid_lineup(
                context.author.id,
                self.database_path,
            )
        except TeamError:
            lineup = get_lineup(context.author.id, self.database_path)
            embed.description = (
                f"Escalação: {len(lineup)}/11 titulares compatíveis. "
                "Use `mm!escalar <jogador>` para completar."
            )
            await context.reply(embed=embed, mention_author=False)
            return
        image = render_lineup(valid_team, lineup)
        file = discord.File(image, filename="escalacao.png")
        embed.set_image(url="attachment://escalacao.png")
        await context.reply(embed=embed, file=file, mention_author=False)

    @commands.command(name="escalar")
    async def scale(
        self,
        context: commands.Context,
        *,
        query: str,
    ) -> None:
        ensure_team(context.author.id, context.author.display_name, self.database_path)
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
            normalized_query = query.casefold()
            matches = [
                player
                for player in owned
                if normalized_query in player["name"].casefold()
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
            op = operation.casefold()
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
