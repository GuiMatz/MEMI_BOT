"""Comandos do Pintos Corridos e da Copa IOS (`src.league`) e os jogos diários das 19h.

Todo dia às 19h (Brasília), nos servidores com o campeonato ativado, o bot começa a edição
(se for o dia) e joga tudo o que está marcado até hoje, cada jogo narrado lance a lance:
primeiro as rodadas e a final do Pintos, depois as fases da Copa. No fim vêm o quadro do
dia, a tabela ou a chave atualizada e a agenda; nas finais, o pôster do campeão.

`mm!campeonato` abre um painel com botões (Resumo, Tabela, Agenda, Copa, Campeões) que
troca a página na mesma mensagem. As imagens ficam em `src.league_images`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import date, time, timedelta

import discord
from discord.ext import commands, tasks

from src import league
from src.boards_image import render_league_table
from src.clock import BRASILIA
from src.cogs.matches import score_file
from src.commentary import rivalry_intro, rivalry_line
from src.db import DATABASE_PATH, connect
from src.income import venue_name
from src.league_images import (
    GOLD,
    GREEN,
    render_bracket,
    render_champion,
    render_edition_start,
    render_matchday,
)
from src.narration import narrate
from src.simulation import penalty_events
from src.team_stats import rivalry
from src.teams import get_team

LOGGER = logging.getLogger("memi.league")
GAME_TIME = time(hour=19, minute=0, tzinfo=BRASILIA)
GOLD_COLOR = discord.Color.from_rgb(*GOLD)
GREEN_COLOR = discord.Color.from_rgb(*GREEN)
INTRO = (
    "Dois campeonatos, um depois do outro, com os mesmos times:\n"
    "🐣 **Pintos Corridos**: todos contra todos, só ida, uma rodada por dia. Os 2 primeiros "
    "fazem a final.\n"
    "🏆 **Copa IOS**: logo depois, mata-mata com uma fase por dia. Campeão e vice do Pintos "
    "em lados opostos da chave; empate vai para os pênaltis.\n"
    "Entram sozinhos todos com o time completo (11 titulares), até 16. Jogos às **19h**."
)
Page = tuple[discord.Embed, list[discord.File]]


def _date(edition: dict, day: int) -> date:
    return date.fromisoformat(edition["start_date"]) + timedelta(days=day - 1)


def _day_label(edition: dict, day: int) -> str:
    """Data do dia `day` da edição ("qui, 05/11")."""
    weekdays = ("seg", "ter", "qua", "qui", "sex", "sáb", "dom")
    moment = _date(edition, day)
    return f"{weekdays[moment.weekday()]}, {moment:%d/%m}"


def _title(fixture: dict) -> str:
    if fixture["competition"] == "copa":
        if fixture["stage"] == "final":
            return "FINAL da Copa IOS"
        return f"Copa IOS · {league.STAGE_LABELS[fixture['stage']]}"
    if fixture["stage"] == "final":
        return "FINAL do Pintos Corridos"
    return f"Pintos Corridos · {fixture['stage'].capitalize()}"


def _score(fixture: dict, names: dict) -> str:
    home = names.get(fixture["home_user_id"], "Time")
    away = names.get(fixture["away_user_id"], "Time")
    if fixture["played_at"] is None:
        return f"{home} × {away}"
    line = f"{home} **{fixture['home_goals']} × {fixture['away_goals']}** {away}"
    if fixture["home_penalties"] is not None:
        line += f" (pên. {fixture['home_penalties']} × {fixture['away_penalties']})"
    if fixture["walkover"]:
        line += " · W.O."
    return line


def _matchday_rows(games: list[dict], teams: dict) -> list[dict]:
    """Os jogos no formato de `render_matchday` (sem placar se ainda não jogaram)."""
    rows = []
    for game in games:
        played = game["played_at"] is not None
        rows.append(
            {
                "home": teams.get(game["home_user_id"]),
                "away": teams.get(game["away_user_id"]),
                "home_goals": game["home_goals"] if played else None,
                "away_goals": game["away_goals"] if played else None,
                "penalties": (
                    (game["home_penalties"], game["away_penalties"])
                    if game["home_penalties"] is not None
                    else None
                ),
                "walkover": bool(game["walkover"]),
            }
        )
    return rows


async def _file(render, *args, name: str) -> discord.File:
    image = await asyncio.to_thread(render, *args)
    return discord.File(image, filename=name)


class LeaguePanel(discord.ui.View):
    """Painel do `mm!campeonato`: cada botão troca a página na mesma mensagem."""

    PAGES = (
        ("resumo", "Resumo", "🏠"),
        ("tabela", "Tabela", "🐣"),
        ("agenda", "Agenda", "📅"),
        ("copa", "Copa IOS", "🏆"),
        ("campeoes", "Campeões", "📜"),
    )

    def __init__(self, cog: "LeagueCog", guild_id: int, owner, page: str = "resumo"):
        super().__init__(timeout=300)
        self.cog = cog
        self.guild_id = guild_id
        self.owner = owner
        self.message: discord.Message | None = None
        for key, label, emoji in self.PAGES:
            button = discord.ui.Button(
                label=label,
                emoji=emoji,
                style=discord.ButtonStyle.primary if key == page else discord.ButtonStyle.secondary,
                custom_id=f"campeonato:{key}",
            )
            button.callback = self._switch(key)
            self.add_item(button)

    def _mark(self, page: str) -> None:
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                active = item.custom_id == f"campeonato:{page}"
                item.style = (
                    discord.ButtonStyle.primary if active else discord.ButtonStyle.secondary
                )

    def _switch(self, page: str):
        async def callback(interaction: discord.Interaction) -> None:
            if interaction.user.id != self.owner.id:
                await interaction.response.send_message(
                    "Esse painel é de quem abriu. Use `mm!campeonato` para abrir o seu.",
                    ephemeral=True,
                )
                return
            await interaction.response.defer()
            embed, files = await self.cog.page(page, self.guild_id, self.owner)
            self._mark(page)
            await interaction.edit_original_response(embed=embed, attachments=files, view=self)

        return callback

    async def on_timeout(self) -> None:
        if self.message is None:
            return
        for item in self.children:
            item.disabled = True
        try:
            await self.message.edit(view=self)
        except discord.HTTPException:
            pass


class LeagueCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.database_path = getattr(bot, "database_path", DATABASE_PATH)
        self.locks: dict[str, asyncio.Lock] = {}
        self.scheduler_enabled = os.getenv("MEMI_CHAMPIONSHIP_SCHEDULER") == "1"
        if self.scheduler_enabled:
            self.daily_games.start()

    def cog_unload(self) -> None:
        if self.daily_games.is_running():
            self.daily_games.cancel()

    def _teams(self) -> dict[str, dict]:
        return league.team_info(self.database_path)

    def _names(self) -> dict[str, str]:
        return {user: team["team_name"] for user, team in self._teams().items()}

    # O dia de jogos ------------------------------------------------------------------------

    async def _members_ready(self, guild: discord.Guild) -> list[str]:
        """Quem tem time completo e está neste servidor (sem a intent de membros, pergunta um a
        um ao Discord)."""
        ready = await asyncio.to_thread(league.eligible_users, self.database_path)
        members = []
        for user_id in ready:
            member = guild.get_member(int(user_id))
            if member is None:
                try:
                    member = await guild.fetch_member(int(user_id))
                except (discord.NotFound, discord.HTTPException):
                    continue
            if not member.bot:
                members.append(user_id)
        return members

    async def run_day(self, guild: discord.Guild, send, *, manual: bool = False) -> None:
        """Começa a edição (se for o dia) e joga tudo o que está marcado até hoje."""
        lock = self.locks.setdefault(str(guild.id), asyncio.Lock())
        if lock.locked():
            if manual:
                await send(content="Os jogos de hoje já estão rolando.")
            return
        async with lock:
            edition = league.ensure_edition(guild.id, self.database_path)
            if edition["status"] == "registration":
                try:
                    edition = league.start_edition(
                        guild.id,
                        await self._members_ready(guild),
                        self.database_path,
                        force=manual,
                    )
                except league.LeagueError as error:
                    if manual:
                        await send(content=str(error))
                    else:
                        LOGGER.info("Edição não começou no servidor %s: %s", guild.id, error)
                    return
                await self._announce_start(send, edition)
            due = league.due_fixtures(edition, database_path=self.database_path)
            if not due:
                if manual:
                    await send(content="Não há jogos marcados para hoje.")
                return
            stages = []
            for fixture in due:
                label = _title(fixture)
                if label not in stages:
                    stages.append(label)
            await send(
                content=f"📅 **Dia {league.day_number(edition)} da edição {edition['number']}** · "
                + " · ".join(stages)
            )
            names = self._names()
            for fixture in due:
                await self._play_and_narrate(send, fixture, names)
            news = await asyncio.to_thread(league.advance, edition, self.database_path)
            await self._day_summary(send, edition, due, news)

    async def _announce_start(self, send, edition: dict) -> None:
        teams = self._teams()
        entries = league.entries(edition["id"], self.database_path)
        playing = [teams.get(e["discord_user_id"]) or e for e in entries if not e["waitlisted"]]
        waiting = [teams.get(e["discord_user_id"]) or e for e in entries if e["waitlisted"]]
        rounds = league.rounds_total(edition["id"], self.database_path)
        calendar = (
            f"{rounds} rodadas · final do Pintos {_date(edition, rounds + 1):%d/%m} · "
            "Copa IOS em seguida"
        )
        file = await _file(
            render_edition_start,
            playing,
            edition["number"],
            calendar,
            len(waiting),
            name="edicao.png",
        )
        embed = discord.Embed(
            title=f"🏁 Começou a edição {edition['number']}!",
            description=INTRO,
            color=GREEN_COLOR,
        )
        if waiting:
            embed.add_field(
                name="Lista de espera (entram primeiro na próxima)",
                value=", ".join(team["team_name"] for team in waiting)[:1024],
                inline=False,
            )
        embed.set_image(url="attachment://edicao.png")
        await send(embed=embed, file=file)

    async def _play_and_narrate(self, send, fixture: dict, names: dict) -> None:
        home_id, away_id = fixture["home_user_id"], fixture["away_user_id"]
        before = await asyncio.to_thread(rivalry, home_id, away_id, self.database_path)
        played = await asyncio.to_thread(league.play_fixture, fixture, None, self.database_path)
        title = _title(played)
        home_team = get_team(home_id, self.database_path) or {"team_name": names.get(home_id)}
        away_team = get_team(away_id, self.database_path) or {"team_name": names.get(away_id)}
        home, away = home_team["team_name"], away_team["team_name"]
        if played["walkover"]:
            absent = {"mandante": home, "visitante": away, "ambos": f"{home} e {away}"}
            await send(
                content=f"🏳️ **{title}** · {_score(played, names)}\n"
                f"{absent[played['walkover']]} sem escalação válida: W.O."
            )
            return
        events = json.loads(played["events_json"] or "{}")
        timeline = list(events.get("events", []))
        kicks = events.get("penalty_kicks") or []
        penalties = None
        if kicks:
            timeline += penalty_events(kicks, home, away)
            penalties = (played["home_penalties"], played["away_penalties"])
        after = await asyncio.to_thread(rivalry, home_id, away_id, self.database_path)
        note = rivalry_line(after, home, away)
        await send(
            content=f"🏟️ {title} · Local: **{venue_name(home_team)}**\n"
            f"{rivalry_intro(before, home, away)}"
        )
        await narrate(
            lambda embed: send(embed=embed),
            home,
            away,
            timeline,
            title=title,
            penalties=penalties,
            home_color=home_team.get("color"),
            away_color=away_team.get("color"),
            notes=[note] if note else (),
        )
        if played["stage"] == "final":
            await send(
                file=await score_file(
                    home_team,
                    away_team,
                    int(played["home_goals"]),
                    int(played["away_goals"]),
                    events.get("goals", []),
                    title=title,
                    venue=venue_name(home_team),
                    penalties=penalties,
                    kicks=kicks,
                )
            )

    async def _day_summary(self, send, edition: dict, played: list[dict], news: dict) -> None:
        """Quadro de cada competição do dia + tabela (Pintos) ou chave (Copa), e a agenda."""
        teams = self._teams()
        names = {user: team["team_name"] for user, team in teams.items()}
        games = league.fixtures(edition["id"], self.database_path)
        by_id = {game["id"]: game for game in games}
        today = [by_id[game["id"]] for game in played]
        day = max((g["day"] for g in today), default=league.day_number(edition))
        upcoming = [g for g in games if g["day"] == day + 1 and g["played_at"] is None]
        for competition in ("pintos", "copa"):
            mine = [g for g in today if g["competition"] == competition]
            if not mine:
                continue
            accent, color = (GREEN, GREEN_COLOR) if competition == "pintos" else (GOLD, GOLD_COLOR)
            heading = _title(mine[0]).replace("Pintos Corridos · ", "").replace("Copa IOS · ", "")
            subtitle = (
                f"{'Pintos Corridos' if competition == 'pintos' else 'Copa IOS'} · "
                f"Edição {edition['number']} · {_day_label(edition, day)}"
            )
            board = await _file(
                render_matchday,
                _matchday_rows(mine, teams),
                heading.upper(),
                subtitle,
                accent,
                name=f"dia-{competition}.png",
            )
            first = discord.Embed(title=f"📋 Resultados do dia · {heading}", color=color)
            first.set_image(url=f"attachment://dia-{competition}.png")
            files = [board]
            embeds = [first]
            if competition == "pintos" and mine[0]["stage"] != "final":
                embed, table = await self._table_page(edition)
                embeds.append(embed)
                files += table
            elif competition == "copa":
                embed, chart = await self._cup_page(edition)
                embeds.append(embed)
                files += chart
            later = [g for g in upcoming if g["competition"] == competition]
            if later:
                embeds[-1].add_field(
                    name=f"📅 Amanhã ({_day_label(edition, day + 1)}), 19h",
                    value="\n".join(f"{_title(g)}: {_score(g, names)}" for g in later)[:1024],
                    inline=False,
                )
            await send(embeds=embeds, files=files)
        for competition in ("pintos", "copa"):
            if news["prizes"].get(competition):
                await self._announce_champion(send, edition, competition, news, teams)
        if news.get("copa_draw"):
            embed, files = await self._cup_page(edition)
            embed.title = "🏆 Sorteio da Copa IOS"
            first_day = min(
                g["day"]
                for g in league.fixtures(edition["id"], self.database_path)
                if g["competition"] == "copa"
            )
            embed.description = (
                f"Começa {_day_label(edition, first_day)}, às 19h. Campeão e vice do Pintos "
                "ficam em lados opostos: só podem se cruzar na final."
            )
            await send(embed=embed, files=files)
        if news.get("next_edition"):
            nxt = news["next_edition"]
            opens = date.fromisoformat(nxt["opens_on"])
            await send(
                content=f"🔜 A edição {nxt['number']} começa **{opens:%d/%m} às 19h**. Entram "
                "todos com o time completo (11 titulares)."
            )

    async def _announce_champion(self, send, edition, competition, news, teams) -> None:
        names = {user: team["team_name"] for user, team in teams.items()}
        prizes = news["prizes"][competition]
        champion = news.get(f"{competition}_champion")
        final = next(
            g
            for g in league.fixtures(edition["id"], self.database_path)
            if g["competition"] == competition and g["stage"] == "final"
        )
        vice = final["away_user_id"] if champion == final["home_user_id"] else final["home_user_id"]
        line = (
            f"Final: {names.get(final['home_user_id'], 'Time')} {final['home_goals']} × "
            f"{final['away_goals']} {names.get(final['away_user_id'], 'Time')}"
        )
        if final["home_penalties"] is not None:
            line += f" (pên. {final['home_penalties']} × {final['away_penalties']})"
        own = next((p for p in prizes if p["user_id"] == champion), None)
        label = "Pintos Corridos" if competition == "pintos" else "Copa IOS"
        poster = await _file(
            render_champion,
            teams.get(champion) or {"team_name": "Time"},
            label,
            edition["number"],
            line,
            teams.get(vice),
            league.prize_plain(own) if own else "",
            name="campeao.png",
        )
        embed = discord.Embed(
            title=f"🎉 {names.get(champion, 'Time')} é campeão da {label}!",
            description="**Prêmios**\n"
            + "\n".join(
                f"{league.PLACE_LABELS.get(p['place'], p['place'])}: "
                f"**{names.get(p['user_id'], 'Time')}** · {league.prize_text(p)}"
                for p in prizes
            ),
            color=GOLD_COLOR,
        )
        embed.set_image(url="attachment://campeao.png")
        await send(embed=embed, file=poster)

    # Páginas (painel e comandos) -----------------------------------------------------------

    def _shown_edition(self, guild_id: int) -> dict | None:
        """A edição em andamento ou, entre uma e outra, a última que terminou."""
        edition = league.current_edition(guild_id, self.database_path)
        if edition and edition["status"] == "registration":
            with connect(self.database_path) as connection:
                row = connection.execute(
                    "SELECT * FROM league_editions WHERE guild_id = ? AND number = ?",
                    (str(guild_id), edition["number"] - 1),
                ).fetchone()
            return dict(row) if row else None
        return edition

    async def page(self, key: str, guild_id: int, user) -> Page:
        if key == "campeoes":
            return self._champions_page(guild_id)
        if key == "resumo":
            return self._summary_page(guild_id, user)
        edition = self._shown_edition(guild_id)
        if edition is None:
            embed = discord.Embed(
                title="🏆 Campeonatos",
                description="Ainda não teve campeonato por aqui. A primeira edição começa às "
                "19h, quando houver pelo menos 4 times completos.",
                color=GOLD_COLOR,
            )
            return embed, []
        if key == "tabela":
            return await self._table_page(edition)
        if key == "agenda":
            return await self._agenda_page(edition, user)
        return await self._cup_page(edition)

    def _summary_page(self, guild_id: int, user) -> Page:
        edition = league.ensure_edition(guild_id, self.database_path)
        embed = discord.Embed(
            title=f"🏆 Campeonatos · Edição {edition['number']}",
            description=INTRO,
            color=GOLD_COLOR,
        )
        names = self._names()
        if edition["status"] == "registration":
            opens = date.fromisoformat(edition["opens_on"])
            embed.add_field(
                name="⏳ Ainda não começou",
                value=f"Começa **{opens:%d/%m} às 19h** (precisa de pelo menos "
                f"{league.MIN_TEAMS} times completos).",
                inline=False,
            )
            return embed, []
        rounds = league.rounds_total(edition["id"], self.database_path)
        table = league.standings(edition["id"], self.database_path)
        games = league.fixtures(edition["id"], self.database_path)
        if table:
            leader = table[0]
            played = max(team["played"] for team in table)
            embed.add_field(
                name="🐣 Pintos Corridos",
                value=f"{played} de {rounds} rodadas · Líder: **{leader['team_name']}** "
                f"({leader['points']} pts)",
                inline=False,
            )
        copa = [g for g in games if g["competition"] == "copa"]
        if copa:
            last = copa[-1]
            status = (
                f"Campeão: **{names.get(last['winner_user_id'], 'Time')}**"
                if last["stage"] == "final" and last["played_at"]
                else f"Fase atual: **{league.STAGE_LABELS[last['stage']]}**"
            )
        else:
            status = "Começa no dia seguinte à final do Pintos."
        embed.add_field(name="🏆 Copa IOS", value=status, inline=False)
        mine = [
            g
            for g in games
            if g["played_at"] is None and str(user.id) in (g["home_user_id"], g["away_user_id"])
        ]
        if mine:
            nxt = mine[0]
            embed.add_field(
                name="📅 Seu próximo jogo",
                value=f"{_day_label(edition, nxt['day'])}, 19h · {_title(nxt)}\n"
                f"{_score(nxt, names)}",
                inline=False,
            )
        return embed, []

    async def _table_page(self, edition: dict) -> Page:
        table = league.standings(edition["id"], self.database_path)
        rounds = league.rounds_total(edition["id"], self.database_path)
        played = max((team["played"] for team in table), default=0)
        subtitle = (
            f"Edição {edition['number']} · {played} de {rounds} rodadas · "
            "os 2 primeiros vão à final"
        )
        file = await _file(
            render_league_table, table, "PINTOS CORRIDOS", subtitle, name="tabela.png"
        )
        embed = discord.Embed(title="🐣 Pintos Corridos · Tabela", color=GREEN_COLOR)
        embed.set_image(url="attachment://tabela.png")
        return embed, [file]

    async def _cup_page(self, edition: dict) -> Page:
        rounds = league.bracket(edition["id"], self.database_path)
        embed = discord.Embed(title=f"🏆 Copa IOS · Edição {edition['number']}", color=GOLD_COLOR)
        if not rounds:
            embed.description = (
                "A Copa IOS começa no dia seguinte à final do Pintos Corridos, com o campeão e o "
                "vice em lados opostos da chave."
            )
            return embed, []
        labels = {r["day"]: f"{_date(edition, r['day']):%d/%m}" for r in rounds}
        pending = [r for r in rounds if any(g is None or not g["played_at"] for g in r["games"])]
        subtitle = f"Edição {edition['number']} · " + (
            f"{league.STAGE_LABELS[pending[0]['stage']]} {_day_label(edition, pending[0]['day'])}"
            if pending
            else "Encerrada"
        )
        file = await _file(
            render_bracket, rounds, self._teams(), "COPA IOS", subtitle, labels, name="chave.png"
        )
        embed.set_image(url="attachment://chave.png")
        return embed, [file]

    async def _agenda_page(self, edition: dict, user) -> Page:
        teams = self._teams()
        names = {key: team["team_name"] for key, team in teams.items()}
        games = [
            g for g in league.fixtures(edition["id"], self.database_path) if g["played_at"] is None
        ]
        mine = [g for g in games if str(user.id) in (g["home_user_id"], g["away_user_id"])]
        embed = discord.Embed(
            title=f"📅 Agenda · {names.get(str(user.id), user.display_name)}",
            description="\n".join(
                f"**{_day_label(edition, g['day'])}** · {_title(g)}\n{_score(g, names)}"
                for g in mine[:8]
            )
            or "Nenhum jogo seu marcado.",
            color=GREEN_COLOR,
        )
        if not games:
            return embed, []
        day = games[0]["day"]
        nxt = [g for g in games if g["day"] == day]
        heading = _title(nxt[0]).replace("Pintos Corridos · ", "").replace("Copa IOS · ", "")
        copa = nxt[0]["competition"] == "copa"
        file = await _file(
            render_matchday,
            _matchday_rows(nxt, teams),
            heading.upper(),
            f"{'Copa IOS' if copa else 'Pintos Corridos'} · {_day_label(edition, day)} · 19h",
            GOLD if copa else GREEN,
            name="agenda.png",
        )
        embed.add_field(name="Próximo dia de jogos", value=_day_label(edition, day), inline=False)
        embed.set_image(url="attachment://agenda.png")
        return embed, [file]

    def _champions_page(self, guild_id: int) -> Page:
        editions = league.history(guild_id, self.database_path)
        lines = [
            f"**Edição {e['number']}** · 🐣 {e.get('pintos_campeao', '—')} "
            f"(vice {e.get('pintos_vice', '—')}) · 🏆 {e.get('copa_campeao', '—')}"
            for e in editions[:20]
        ]
        embed = discord.Embed(
            title="📜 Campeões",
            description="\n".join(lines) or "Nenhuma edição terminou ainda.",
            color=GOLD_COLOR,
        )
        return embed, []

    async def _open(self, context: commands.Context, page: str) -> None:
        if context.guild is None:
            await context.reply("O campeonato é de cada servidor.", mention_author=False)
            return
        embed, files = await self.page(page, context.guild.id, context.author)
        view = LeaguePanel(self, context.guild.id, context.author, page)
        embed.set_footer(text="admin: mm!campeonato canal · ativar · jogar")
        view.message = await context.reply(
            embed=embed, files=files, view=view, mention_author=False
        )

    # Agendador -----------------------------------------------------------------------------

    @tasks.loop(time=GAME_TIME)
    async def daily_games(self) -> None:
        with connect(self.database_path) as connection:
            settings = [
                dict(row)
                for row in connection.execute(
                    "SELECT guild_id, channel_id FROM championship_settings WHERE enabled = 1"
                )
            ]
        for setting in settings:
            guild = self.bot.get_guild(int(setting["guild_id"]))
            channel = self.bot.get_channel(int(setting["channel_id"]))
            if guild is None or not isinstance(channel, discord.TextChannel):
                LOGGER.warning("Servidor ou canal do campeonato indisponível: %s", setting)
                continue
            try:
                await self.run_day(guild, channel.send)
            except Exception:  # um servidor com problema não pode travar os outros
                LOGGER.exception("Falha nos jogos do servidor %s", setting["guild_id"])

    @daily_games.before_loop
    async def before_daily_games(self) -> None:
        await self.bot.wait_until_ready()

    # Comandos ------------------------------------------------------------------------------

    @commands.group(
        name="campeonato", aliases=["campeonatos", "pintos"], invoke_without_command=True
    )
    async def championship(self, context: commands.Context) -> None:
        """Painel do campeonato: resumo, tabela, agenda, Copa e campeões em botões."""
        await self._open(context, "resumo")

    @championship.command(name="tabela", aliases=["classificacao", "classificação"])
    async def table(self, context: commands.Context) -> None:
        await self._open(context, "tabela")

    @championship.command(name="agenda", aliases=["jogos", "proximos", "próximos"])
    async def agenda(self, context: commands.Context, member: discord.Member | None = None) -> None:
        if context.guild is None:
            await context.reply("O campeonato é de cada servidor.", mention_author=False)
            return
        target = member or context.author
        embed, files = await self.page("agenda", context.guild.id, target)
        view = LeaguePanel(self, context.guild.id, context.author, "agenda")
        view.message = await context.reply(
            embed=embed, files=files, view=view, mention_author=False
        )

    @championship.command(name="copa", aliases=["chave", "ios"])
    async def cup(self, context: commands.Context) -> None:
        await self._open(context, "copa")

    @championship.command(name="historico", aliases=["histórico", "campeoes", "campeões"])
    async def past(self, context: commands.Context) -> None:
        await self._open(context, "campeoes")

    def _is_admin(self, context: commands.Context) -> bool:
        return bool(context.guild) and context.author.guild_permissions.manage_guild

    @championship.command(name="canal")
    async def channel(
        self, context: commands.Context, channel: discord.TextChannel | None = None
    ) -> None:
        if not self._is_admin(context):
            await context.reply(
                "É preciso ter a permissão Gerenciar servidor.", mention_author=False
            )
            return
        target = channel or context.channel
        if not isinstance(target, discord.TextChannel):
            await context.reply("Use um canal de texto do servidor.", mention_author=False)
            return
        with connect(self.database_path) as connection:
            connection.execute(
                """
                INSERT INTO championship_settings (guild_id, channel_id, enabled)
                VALUES (?, ?, 0)
                ON CONFLICT(guild_id) DO UPDATE SET
                    channel_id = excluded.channel_id, updated_at = CURRENT_TIMESTAMP
                """,
                (str(context.guild.id), str(target.id)),
            )
        await context.reply(
            f"Canal do campeonato: {target.mention}. Ative os jogos diários com "
            "`mm!campeonato ativar`.",
            mention_author=False,
        )

    @championship.command(name="ativar")
    async def enable(self, context: commands.Context) -> None:
        if not self._is_admin(context):
            await context.reply(
                "É preciso ter a permissão Gerenciar servidor.", mention_author=False
            )
            return
        if not self.scheduler_enabled:
            await context.reply(
                "O agendador está desligado. Defina `MEMI_CHAMPIONSHIP_SCHEDULER=1` e reinicie o bot.",
                mention_author=False,
            )
            return
        with connect(self.database_path) as connection:
            cursor = connection.execute(
                "UPDATE championship_settings SET enabled = 1, updated_at = CURRENT_TIMESTAMP "
                "WHERE guild_id = ?",
                (str(context.guild.id),),
            )
        if not cursor.rowcount:
            await context.reply(
                "Configure antes o canal com `mm!campeonato canal`.", mention_author=False
            )
            return
        league.ensure_edition(context.guild.id, self.database_path)
        await context.reply(
            "Jogos diários ativados: todo dia às 19h (Brasília).", mention_author=False
        )

    @championship.command(name="jogar", aliases=["iniciar"])
    async def play_now(self, context: commands.Context) -> None:
        """Admin: começa a edição agora (se estiver esperando) e joga os jogos de hoje."""
        if not self._is_admin(context):
            await context.reply(
                "É preciso ter a permissão Gerenciar servidor.", mention_author=False
            )
            return
        await self.run_day(context.guild, lambda **kwargs: context.send(**kwargs), manual=True)

    @commands.command(name="temporada", aliases=["season", "tabela"])
    async def season(self, context: commands.Context) -> None:
        await self._open(context, "tabela")

    @commands.command(name="temporadas", aliases=["seasons", "campeoes", "campeões"])
    async def seasons(self, context: commands.Context) -> None:
        await self._open(context, "campeoes")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(LeagueCog(bot))
