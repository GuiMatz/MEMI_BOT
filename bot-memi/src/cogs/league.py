"""Comandos do Pintos Corridos e da Copa IOS (`src.league`) e os jogos diários das 19h.

Todo dia às 19h (Brasília), nos servidores com o campeonato ativado, o bot começa a edição
(se for o dia) e joga tudo o que está marcado até hoje, cada jogo narrado lance a lance:
primeiro as rodadas e a final do Pintos, depois as fases da Copa. No fim vêm os resultados,
a tabela e a agenda.
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
from src.narration import narrate
from src.simulation import penalty_events
from src.team_stats import rivalry
from src.teams import get_team

LOGGER = logging.getLogger("memi.league")
GAME_TIME = time(hour=19, minute=0, tzinfo=BRASILIA)
INTRO = (
    "Dois campeonatos, um depois do outro, com os mesmos times:\n"
    "🐣 **Pintos Corridos**: todos contra todos, só ida, uma rodada por dia. Os 2 primeiros "
    "fazem a final.\n"
    "🏆 **Copa IOS**: logo depois, mata-mata com uma fase por dia. Campeão e vice do Pintos "
    "em lados opostos da chave; empate vai para os pênaltis.\n"
    "Entram sozinhos todos com o time completo (11 titulares), até 16. Jogos às **19h**."
)


def _day_label(edition: dict, day: int) -> str:
    """Data do dia `day` da edição ("qui, 05/11")."""
    start = date.fromisoformat(edition["start_date"])
    moment = start + timedelta(days=day - 1)
    weekdays = ("seg", "ter", "qua", "qui", "sex", "sáb", "dom")
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

    def _names(self) -> dict[str, str]:
        return {
            user: team["team_name"] for user, team in league.team_info(self.database_path).items()
        }

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
            day = league.day_number(edition)
            stages = []
            for fixture in due:
                label = _title(fixture)
                if label not in stages:
                    stages.append(label)
            await send(
                content=f"📅 **Dia {day} da edição {edition['number']}** · " + " · ".join(stages)
            )
            names = self._names()
            for fixture in due:
                await self._play_and_narrate(send, fixture, names)
            news = await asyncio.to_thread(league.advance, edition, self.database_path)
            await self._day_summary(send, edition, due, news)

    async def _announce_start(self, send, edition: dict) -> None:
        names = self._names()
        entries = league.entries(edition["id"], self.database_path)
        playing = [
            names.get(e["discord_user_id"], e["team_name"]) for e in entries if not e["waitlisted"]
        ]
        waiting = [
            names.get(e["discord_user_id"], e["team_name"]) for e in entries if e["waitlisted"]
        ]
        rounds = league.rounds_total(edition["id"], self.database_path)
        embed = discord.Embed(
            title=f"🏁 Começou a edição {edition['number']}!",
            description=INTRO,
            color=discord.Color.gold(),
        )
        embed.add_field(
            name=f"Times ({len(playing)})", value=", ".join(playing)[:1024], inline=False
        )
        embed.add_field(
            name="Calendário",
            value=f"Pintos Corridos: {rounds} rodadas e a final no dia {rounds + 1} "
            f"({_day_label(edition, rounds + 1)}). A Copa IOS começa no dia seguinte.",
            inline=False,
        )
        if waiting:
            embed.add_field(
                name="Lista de espera (entram primeiro na próxima)",
                value=", ".join(waiting)[:1024],
                inline=False,
            )
        await send(embed=embed)

    async def _announce_cup(self, send, edition: dict) -> None:
        """O sorteio da Copa IOS, logo depois da final do Pintos."""
        names = self._names()
        draw = [
            f
            for f in league.fixtures(edition["id"], self.database_path)
            if f["competition"] == "copa"
        ]
        if not draw:
            return
        half = len(draw) // 2
        sides = []
        for label, games in (("Lado A", draw[:half]), ("Lado B", draw[half:])):
            lines = [
                (
                    f"{names.get(f['home_user_id'], 'Time')} passa direto"
                    if f["walkover"] == "folga"
                    else _score(f, names)
                )
                for f in games
            ]
            sides.append((label, lines))
        embed = discord.Embed(
            title=f"🏆 Sorteio da Copa IOS · {league.STAGE_LABELS[draw[0]['stage']]}",
            description=f"Começa {_day_label(edition, draw[0]['day'])}, às 19h. Campeão e vice "
            "do Pintos ficam em lados opostos: só podem se cruzar na final.",
            color=discord.Color.gold(),
        )
        for label, lines in sides:
            embed.add_field(name=label, value="\n".join(lines)[:1024] or "—", inline=True)
        await send(embed=embed)

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
        names = self._names()
        games = league.fixtures(edition["id"], self.database_path)
        by_id = {game["id"]: game for game in games}
        today = [by_id[game["id"]] for game in played]
        embed = discord.Embed(title="📋 Resultados do dia", color=discord.Color.gold())
        for competition, label in (("copa", "🏆 Copa IOS"), ("pintos", "🐣 Pintos Corridos")):
            lines = [_score(g, names) for g in today if g["competition"] == competition]
            if lines:
                embed.add_field(name=label, value="\n".join(lines)[:1024], inline=False)
        day = max((g["day"] for g in today), default=league.day_number(edition))
        upcoming = [g for g in games if g["day"] == day + 1 and g["played_at"] is None]
        if upcoming:
            embed.add_field(
                name=f"📅 Amanhã ({_day_label(edition, day + 1)}), 19h",
                value="\n".join(f"{_title(g)}: {_score(g, names)}" for g in upcoming)[:1024],
                inline=False,
            )
        file = None
        if any(g["competition"] == "pintos" and g["stage"] != "final" for g in today):
            file = await self._table_file(edition)
            embed.set_image(url="attachment://tabela.png")
        await send(embed=embed, **({"file": file} if file else {}))
        for competition, title in (("copa", "🏆 Copa IOS"), ("pintos", "🐣 Pintos Corridos")):
            prizes = news["prizes"].get(competition)
            if not prizes:
                continue
            champion = news.get(f"{competition}_champion")
            lines = [
                f"{league.PLACE_LABELS.get(p['place'], p['place'])}: "
                f"**{names.get(p['user_id'], 'Time')}** · {league.prize_text(p)}"
                for p in prizes
            ]
            await send(
                content=f"🎉 **{names.get(champion, 'Time')}** é o campeão da {title}!\n\n"
                "**Prêmios**\n" + "\n".join(lines)
            )
        if news.get("copa_draw"):
            await self._announce_cup(send, edition)
        if news.get("next_edition"):
            nxt = news["next_edition"]
            opens = date.fromisoformat(nxt["opens_on"])
            await send(
                content=f"🔜 A edição {nxt['number']} começa **{opens:%d/%m} às 19h**. Entram "
                "todos com o time completo (11 titulares)."
            )

    async def _table_file(self, edition: dict) -> discord.File:
        table = league.standings(edition["id"], self.database_path)
        rounds = league.rounds_total(edition["id"], self.database_path)
        played = max((team["played"] for team in table), default=0)
        subtitle = f"Edição {edition['number']} · {played} de {rounds} rodadas · os 2 primeiros vão à final"
        image = await asyncio.to_thread(render_league_table, table, "PINTOS CORRIDOS", subtitle)
        return discord.File(image, filename="tabela.png")

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
        """Resumo da edição: dia, líder, fase da Copa e o seu próximo jogo."""
        if context.guild is None:
            await context.reply("O campeonato é de cada servidor.", mention_author=False)
            return
        edition = league.ensure_edition(context.guild.id, self.database_path)
        embed = discord.Embed(
            title=f"🏆 Campeonatos · Edição {edition['number']}",
            description=INTRO,
            color=discord.Color.gold(),
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
        else:
            day = league.day_number(edition)
            rounds = league.rounds_total(edition["id"], self.database_path)
            table = league.standings(edition["id"], self.database_path)
            if table:
                leader = table[0]
                embed.add_field(
                    name="🐣 Pintos Corridos",
                    value=f"Dia {day} · {rounds} rodadas + final\nLíder: **{leader['team_name']}** "
                    f"({leader['points']} pts)",
                    inline=False,
                )
            games = league.fixtures(edition["id"], self.database_path)
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
                if g["played_at"] is None
                and str(context.author.id) in (g["home_user_id"], g["away_user_id"])
            ]
            if mine:
                nxt = mine[0]
                embed.add_field(
                    name="📅 Seu próximo jogo",
                    value=f"{_day_label(edition, nxt['day'])}, 19h · {_title(nxt)}\n{_score(nxt, names)}",
                    inline=False,
                )
        embed.set_footer(
            text="mm!campeonato tabela · agenda [@pessoa] · copa · historico · "
            "admin: canal, ativar, jogar"
        )
        await context.reply(embed=embed, mention_author=False)

    async def _playing(self, context: commands.Context) -> dict | None:
        if context.guild is None:
            await context.reply("O campeonato é de cada servidor.", mention_author=False)
            return None
        edition = league.current_edition(context.guild.id, self.database_path)
        if edition and edition["status"] == "registration":
            # Entre uma edição e outra, mostra a última que terminou.
            with connect(self.database_path) as connection:
                row = connection.execute(
                    "SELECT * FROM league_editions WHERE guild_id = ? AND number = ?",
                    (str(context.guild.id), edition["number"] - 1),
                ).fetchone()
            edition = dict(row) if row else None
        if edition is None:
            await context.reply(
                "Ainda não teve campeonato por aqui. Veja `mm!campeonato`.", mention_author=False
            )
        return edition

    @championship.command(name="tabela", aliases=["classificacao", "classificação"])
    async def table(self, context: commands.Context) -> None:
        edition = await self._playing(context)
        if edition is None:
            return
        file = await self._table_file(edition)
        await context.reply(file=file, mention_author=False)

    @championship.command(name="agenda", aliases=["jogos", "proximos", "próximos"])
    async def agenda(self, context: commands.Context, member: discord.Member | None = None) -> None:
        edition = await self._playing(context)
        if edition is None:
            return
        target = member or context.author
        names = self._names()
        games = [
            g for g in league.fixtures(edition["id"], self.database_path) if g["played_at"] is None
        ]
        mine = [g for g in games if str(target.id) in (g["home_user_id"], g["away_user_id"])]
        embed = discord.Embed(
            title=f"📅 Agenda · {names.get(str(target.id), target.display_name)}",
            color=discord.Color.gold(),
        )
        embed.description = (
            "\n".join(
                f"**{_day_label(edition, g['day'])}** · {_title(g)}\n{_score(g, names)}"
                for g in mine[:10]
            )
            or "Nenhum jogo marcado."
        )
        if games:
            day = games[0]["day"]
            embed.add_field(
                name=f"Próximo dia de jogos ({_day_label(edition, day)}, 19h)",
                value="\n".join(
                    f"{_title(g)}: {_score(g, names)}" for g in games if g["day"] == day
                )[:1024],
                inline=False,
            )
        await context.reply(embed=embed, mention_author=False)

    @championship.command(name="copa", aliases=["chave", "ios"])
    async def cup(self, context: commands.Context) -> None:
        edition = await self._playing(context)
        if edition is None:
            return
        names = self._names()
        copa = [
            g
            for g in league.fixtures(edition["id"], self.database_path)
            if g["competition"] == "copa"
        ]
        embed = discord.Embed(
            title=f"🏆 Copa IOS · Edição {edition['number']}", color=discord.Color.gold()
        )
        for stage in league.STAGE_LABELS:
            games = [g for g in copa if g["stage"] == stage]
            if not games:
                continue
            lines = []
            for game in games:
                if game["walkover"] == "folga":
                    lines.append(f"{names.get(game['home_user_id'], 'Time')} passou direto")
                    continue
                mark = ""
                if game["winner_user_id"]:
                    mark = f" → **{names.get(game['winner_user_id'], 'Time')}**"
                lines.append(_score(game, names) + mark)
            embed.add_field(
                name=f"{league.STAGE_LABELS[stage]} · {_day_label(edition, games[0]['day'])}",
                value="\n".join(lines)[:1024],
                inline=False,
            )
        await context.reply(embed=embed, mention_author=False)

    @championship.command(name="historico", aliases=["histórico", "campeoes", "campeões"])
    async def past(self, context: commands.Context) -> None:
        if context.guild is None:
            await context.reply("O campeonato é de cada servidor.", mention_author=False)
            return
        editions = league.history(context.guild.id, self.database_path)
        lines = [
            f"**Edição {e['number']}** · 🐣 {e.get('pintos_campeao', '—')} "
            f"(vice {e.get('pintos_vice', '—')}) · 🏆 {e.get('copa_campeao', '—')}"
            for e in editions[:20]
        ]
        embed = discord.Embed(
            title="📜 Campeões",
            description="\n".join(lines) or "Nenhuma edição terminou ainda.",
            color=discord.Color.gold(),
        )
        await context.reply(embed=embed, mention_author=False)

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
        await self.table(context)

    @commands.command(name="temporadas", aliases=["seasons", "campeoes", "campeões"])
    async def seasons(self, context: commands.Context) -> None:
        await self.past(context)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(LeagueCog(bot))
