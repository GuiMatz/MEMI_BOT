"""Comandos de amistoso e campeonato diário."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import time

import discord
from discord.ext import commands, tasks

from src import icons
from src.cogs.penalties import PenaltyChallengeView
from src.wagers import WagerError, check_wager, parse_wager, pay, take_stakes
from src.championship import (
    CHAMPION_PRIZE,
    CHAMPIONSHIP_TIMEZONE,
    ChampionshipError,
    get_championship,
    play_championship,
    register_team,
)
from src.db import DATABASE_PATH, connect, normalize_player_name
from src.income import IncomeError, collect_income, income_status, venue_name
from src.missions import record_event
from src.narration import narrate
from src.score_image import render_score_gif
from src.simulation import penalty_events, penalty_shootout_kicks, simulate_match
from src.team_stats import (
    KINDS,
    PERIODS,
    compute_stats,
    filter_records,
    match_records,
    record_friendly,
)
from src.teams import TeamError, get_team, get_valid_lineup

SET_PIECE_MARK = {"penalti": " (pênalti)", "falta": " (falta)", "contra": " (contra)"}

LOGGER = logging.getLogger("memi.championship")


async def score_file(home, away, home_goals, away_goals, goals, **options) -> discord.File:
    """GIF do placar final (`src.score_image`): amistoso ou campeonato, pelo título."""
    image = await asyncio.to_thread(
        render_score_gif, home, away, home_goals, away_goals, list(goals), **options
    )
    return discord.File(image, filename="placar.gif")


# `mm!amistoso @pessoa idaevolta`: um jogo na casa de cada time, vale o placar somado.
TWO_LEG_WORDS = {"idaevolta", "ida", "iv", "2", "volta"}


def parse_friendly_options(text: str) -> tuple[bool, int, list[str]]:
    """(ida e volta?, aposta, palavras que não entendi) de "idaevolta aposta 1000"."""
    two_legs, wager, unknown = False, 0, []
    for raw in (text or "").split():
        # O valor vem do texto original ("1.000" não pode virar "1 000").
        word = normalize_player_name(raw).replace(" ", "")
        if word in TWO_LEG_WORDS:
            two_legs = True
        elif word in {"aposta", "apostado", "bet", "valendo", "e", "volta"}:
            continue
        elif (value := parse_wager(raw)) is not None:
            wager = value
        else:
            unknown.append(word)
    return two_legs, wager, unknown


class FriendlyWagerView(discord.ui.View):
    """Amistoso apostado: o desafiado aceita (as duas apostas saem) e quem vence leva tudo."""

    def __init__(self, cog, context, opponent, wager: int, two_legs: bool):
        super().__init__(timeout=120)
        self.cog = cog
        self.context = context
        self.opponent = opponent
        self.wager = wager
        self.two_legs = two_legs
        self.message: discord.Message | None = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.opponent.id:
            await interaction.response.send_message(
                "Só quem foi desafiado responde.", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="Aceitar a aposta", emoji="💰", style=discord.ButtonStyle.success)
    async def accept(self, interaction: discord.Interaction, button) -> None:
        self.stop()
        author = self.context.author
        try:
            take_stakes(
                (author.id, author.display_name),
                (self.opponent.id, self.opponent.display_name),
                self.wager,
                self.cog.database_path,
            )
        except WagerError as error:
            await interaction.response.edit_message(content=str(error), view=None)
            return
        await interaction.response.edit_message(
            content=f"💰 Aposta aceita: {self.wager * 2:,} Memilets em jogo!".replace(",", "."),
            view=None,
        )
        await self.cog.play_wagered(self.context, self.opponent, self.wager, self.two_legs)

    @discord.ui.button(label="Recusar", style=discord.ButtonStyle.secondary)
    async def refuse(self, interaction: discord.Interaction, button) -> None:
        self.stop()
        await interaction.response.edit_message(
            content=f"{self.opponent.display_name} recusou a aposta.", view=None
        )

    async def on_timeout(self) -> None:
        if self.message:
            try:
                await self.message.edit(content="Desafio apostado expirou.", view=None)
            except discord.HTTPException:
                pass


class IncomeChoiceView(discord.ui.View):
    """O mandante decide se o amistoso vale a renda do dia."""

    def __init__(self, author_id: int, play):
        super().__init__(timeout=120)
        self.author_id = author_id
        self.play = play
        self.message: discord.Message | None = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "Só o time mandante confirma a renda.", ephemeral=True
            )
            return False
        return True

    async def _start(self, interaction: discord.Interaction, with_income: bool) -> None:
        self.stop()
        await interaction.response.edit_message(
            content="⚽ Bola rolando" + (" valendo renda!" if with_income else "!"), view=None
        )
        await self.play(with_income)

    @discord.ui.button(label="Sim, valendo renda", emoji="🏟️", style=discord.ButtonStyle.success)
    async def with_income(self, interaction: discord.Interaction, button) -> None:
        await self._start(interaction, True)

    @discord.ui.button(label="Jogar sem renda", style=discord.ButtonStyle.primary)
    async def without_income(self, interaction: discord.Interaction, button) -> None:
        await self._start(interaction, False)

    @discord.ui.button(label="Cancelar", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button) -> None:
        self.stop()
        await interaction.response.edit_message(content="Amistoso cancelado.", view=None)

    async def on_timeout(self) -> None:
        if self.message:
            try:
                await self.message.edit(content="Amistoso não confirmado a tempo.", view=None)
            except discord.HTTPException:
                pass


OUTCOME_WORDS = {
    "V": ("vitória", "vitórias"),
    "E": ("empate", "empates"),
    "D": ("derrota", "derrotas"),
}


class StatsView(discord.ui.View):
    """`mm!stats`: números do time com filtros de tipo, período e adversário."""

    def __init__(self, viewer_id: int, owner_id: int, database_path):
        super().__init__(timeout=300)
        self.viewer_id = viewer_id
        self.owner_id = str(owner_id)
        self.database_path = database_path
        self.kind = "all"
        self.period = "all"
        self.opponent: str | None = None
        self.records = match_records(owner_id, database_path)
        with connect(database_path) as connection:
            self.names = {
                row["discord_user_id"]: row["team_name"]
                for row in connection.execute("SELECT discord_user_id, team_name FROM game_teams")
            }
        self.team = get_team(owner_id, database_path) or {}
        self.message: discord.Message | None = None
        self._build()

    def _build(self) -> None:
        self.clear_items()
        kind = discord.ui.Select(
            options=[
                discord.SelectOption(label=label, value=key, default=key == self.kind)
                for key, label in KINDS.items()
            ],
            row=0,
        )
        kind.callback = self._kind
        self.add_item(kind)
        period = discord.ui.Select(
            options=[
                discord.SelectOption(label=label, value=key, default=key == self.period)
                for key, label in PERIODS.items()
            ],
            row=1,
        )
        period.callback = self._period
        self.add_item(period)
        counts: dict[str, int] = {}
        for record in self.records:
            counts[record.opponent_id] = counts.get(record.opponent_id, 0) + 1
        rivals = sorted(counts, key=lambda rival: -counts[rival])[:24]
        opponent = discord.ui.Select(
            placeholder="Contra todos",
            options=[
                discord.SelectOption(
                    label="Contra todos", value="all", default=self.opponent is None
                )
            ]
            + [
                discord.SelectOption(
                    label=f"Contra {self.names.get(rival, 'time removido')}"[:100],
                    description=f"{counts[rival]} jogo(s)",
                    value=rival,
                    default=rival == self.opponent,
                )
                for rival in rivals
            ],
            row=2,
            disabled=not rivals,
        )
        opponent.callback = self._opponent
        self.add_item(opponent)

    def embed(self) -> discord.Embed:
        records = filter_records(
            self.records, kind=self.kind, period=self.period, opponent=self.opponent
        )
        stats = compute_stats(records)
        name = self.team.get("team_name") or "Time"
        scope = [KINDS[self.kind], PERIODS[self.period]]
        if self.opponent:
            scope.append(f"contra {self.names.get(self.opponent, 'time removido')}")
        embed = discord.Embed(
            title=f"📊 {name}",
            description=" · ".join(scope),
            color=discord.Color.from_str(self.team.get("color") or "#2E8B57"),
        )
        if not stats.matches:
            embed.description += "\n\nNenhum jogo nesse recorte ainda."
            if self.kind != "championship":
                embed.set_footer(text="Amistosos passaram a contar a partir desta versão do bot.")
            return embed
        icons_form = {"V": "🟩", "E": "⬜", "D": "🟥"}
        embed.add_field(
            name="Campanha",
            value=(
                f"**{stats.matches}** jogos · **{stats.wins}**V **{stats.draws}**E "
                f"**{stats.losses}**D\n"
                f"Aproveitamento **{stats.win_rate:.0%}** de vitórias"
            ),
            inline=False,
        )
        scored = f"{stats.goals_for / stats.matches:.1f}".replace(".", ",")
        conceded = f"{stats.goals_against / stats.matches:.1f}".replace(".", ",")
        embed.add_field(
            name="Gols",
            value=(
                f"⚽ **{stats.goals_for}** pró · **{stats.goals_against}** contra · "
                f"saldo **{stats.goals_for - stats.goals_against:+d}**\n"
                f"Média **{scored}** × **{conceded}** por jogo · "
                f"🧤 **{stats.clean_sheets}** sem sofrer gol"
            ),
            inline=False,
        )
        moment = []
        if stats.form:
            moment.append("Forma " + "".join(icons_form[result] for result in stats.form))
        outcome, length = stats.streak
        if length >= 2:
            moment.append(
                f"{length} {OUTCOME_WORDS[outcome][1]} seguid{'os' if outcome == 'E' else 'as'}"
            )
        if stats.biggest_win:
            win = stats.biggest_win
            moment.append(
                f"Maior vitória **{win.goals_for} × {win.goals_against}** contra "
                f"{self.names.get(win.opponent_id, 'time removido')}"
            )
        embed.add_field(name="Momento", value="\n".join(moment) or "—", inline=False)
        if stats.top_scorers:
            embed.add_field(
                name="Artilheiros",
                value="\n".join(
                    f"`{index}.` {name} · **{goals}**"
                    for index, (name, goals) in enumerate(stats.top_scorers, 1)
                ),
                inline=True,
            )
        extras = []
        if stats.titles:
            extras.append(f"🏆 **{stats.titles}** título(s)")
        if stats.hat_tricks:
            extras.append(f"🎩 **{stats.hat_tricks}** hat-trick(s)")
        won, lost = stats.shootouts
        if won or lost:
            extras.append(f"🎯 Disputas de pênaltis: **{won}V** · **{lost}D**")
        if extras:
            embed.add_field(name="Feitos", value="\n".join(extras), inline=True)
        if self.kind != "championship":
            embed.set_footer(text="Amistosos contam a partir desta versão do bot.")
        return embed

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.viewer_id:
            await interaction.response.send_message(
                "Use `mm!stats` para abrir o seu painel.", ephemeral=True
            )
            return False
        return True

    async def _refresh(self, interaction: discord.Interaction) -> None:
        self._build()
        await interaction.response.edit_message(embed=self.embed(), view=self)

    async def _kind(self, interaction: discord.Interaction) -> None:
        self.kind = interaction.data["values"][0]
        await self._refresh(interaction)

    async def _period(self, interaction: discord.Interaction) -> None:
        self.period = interaction.data["values"][0]
        await self._refresh(interaction)

    async def _opponent(self, interaction: discord.Interaction) -> None:
        value = interaction.data["values"][0]
        self.opponent = None if value == "all" else value
        await self._refresh(interaction)

    async def on_timeout(self) -> None:
        if self.message:
            try:
                await self.message.edit(view=None)
            except discord.HTTPException:
                pass


class MatchesCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.database_path = getattr(bot, "database_path", DATABASE_PATH)
        self.scheduler_enabled = os.getenv("MEMI_CHAMPIONSHIP_SCHEDULER") == "1"
        if self.scheduler_enabled:
            self.daily_scheduler.start()

    def cog_unload(self) -> None:
        if self.daily_scheduler.is_running():
            self.daily_scheduler.cancel()

    @commands.command(name="stats", aliases=["estatisticas", "estatísticas", "estatistica"])
    async def team_stats(self, context: commands.Context) -> None:
        """Estatísticas do time (ou de `@pessoa`), com filtros em menus."""
        target = context.message.mentions[0] if context.message.mentions else context.author
        if get_team(target.id, self.database_path) is None:
            await context.reply(
                f"{target.display_name} ainda não tem time. Use `mm!time` para criar.",
                mention_author=False,
            )
            return
        view = StatsView(context.author.id, target.id, self.database_path)
        view.message = await context.reply(embed=view.embed(), view=view, mention_author=False)

    @commands.command(name="penaltis", aliases=["pênaltis", "penalti", "penaltys", "pk"])
    async def penalties(
        self, context: commands.Context, opponent: discord.Member, wager: str | None = None
    ) -> None:
        """`mm!penaltis @pessoa [aposta]`: disputa de pênaltis, escolhendo o canto."""
        if opponent.bot or opponent.id == context.author.id:
            await context.reply("Desafie outra pessoa.", mention_author=False)
            return
        amount = 0
        if wager:
            amount = parse_wager(wager) or 0
            try:
                check_wager(amount)
            except WagerError as error:
                await context.reply(str(error), mention_author=False)
                return
        view = PenaltyChallengeView(context.author, opponent, amount, self.database_path)
        stake = (
            f" valendo **{amount:,}** Memilets de cada (quem vence leva {amount * 2:,})".replace(
                ",", "."
            )
            if amount
            else ""
        )
        view.message = await context.reply(
            f"⚽ {opponent.mention}, **{context.author.display_name}** te desafiou para os "
            f"pênaltis{stake}! São 5 cobranças para cada lado: quem bate escolhe o canto e "
            "quem defende escolhe onde pular.",
            view=view,
            mention_author=False,
        )

    @commands.command(name="amistoso")
    async def friendly(
        self,
        context: commands.Context,
        opponent: discord.Member,
        *,
        options: str = "",
    ) -> None:
        """`mm!amistoso @pessoa [idaevolta] [aposta 1000]`."""
        if opponent.bot or opponent.id == context.author.id:
            await context.reply(
                "Escolha outra pessoa com um time configurado.",
                mention_author=False,
            )
            return
        two_legs, wager, unknown = parse_friendly_options(options)
        if unknown:
            await context.reply(
                "Use `mm!amistoso @pessoa` (jogo único), `mm!amistoso @pessoa idaevolta` "
                "(um jogo na casa de cada time) e, para apostar, `aposta 1000` no fim: "
                "`mm!amistoso @pessoa idaevolta aposta 1000`.",
                mention_author=False,
            )
            return
        try:
            if wager:
                check_wager(wager)
            get_valid_lineup(context.author.id, self.database_path)
            get_valid_lineup(opponent.id, self.database_path)
        except (TeamError, WagerError) as error:
            await context.reply(str(error), mention_author=False)
            return
        if wager:
            # Apostado: o outro aceita (e as duas apostas saem); não vale renda.
            view = FriendlyWagerView(self, context, opponent, wager, two_legs)
            kind = "um ida e volta" if two_legs else "um amistoso"
            view.message = await context.reply(
                f"💰 {opponent.mention}, **{context.author.display_name}** te desafiou para "
                f"{kind} valendo **{wager:,}** Memilets de cada (quem vence leva "
                f"{wager * 2:,}). Amistoso apostado não gera renda.".replace(",", "."),
                view=view,
                mention_author=False,
            )
            return

        async def play(with_income: bool) -> None:
            if two_legs:
                await self._play_two_legs(context, opponent, with_income)
            else:
                await self._play_friendly(context, opponent, with_income)

        if not income_status(context.author.id, self.database_path).ready:
            await play(False)
            return
        view = IncomeChoiceView(context.author.id, play)
        view.message = await context.reply(
            "🏟️ Esse amistoso vai gerar renda para o clube, tem certeza?\n"
            + ("No ida e volta, a renda é só do jogo de ida (na sua casa). " if two_legs else "")
            + "Depois dele, a próxima renda só sai em **24 h** (amistosos sem renda continuam "
            "liberados). Regras em `mm!renda`.",
            view=view,
            mention_author=False,
        )

    async def _leg(
        self, context: commands.Context, home_id: int, away_id: int, title: str, shootout
    ):
        """Um jogo narrado, com o placar no fim. `shootout(result)` diz se vai para os pênaltis.

        Devolve (resultado, pênaltis ou None), ou None se algum time ficou inválido."""
        try:
            home, home_players = get_valid_lineup(home_id, self.database_path)
            away, away_players = get_valid_lineup(away_id, self.database_path)
        except TeamError as error:
            await context.send(str(error))
            return None
        result = simulate_match(
            home["team_name"],
            home_players,
            away["team_name"],
            away_players,
            home_style=home["style"],
            away_style=away["style"],
            home_tactics=home,
            away_tactics=away,
            home_advantage=True,
        )
        timeline = list(result.events)
        penalties = None
        if shootout(result):
            kicks = penalty_shootout_kicks(
                home_players,
                away_players,
                home_taker_id=home.get("penalty_player_id"),
                away_taker_id=away.get("penalty_player_id"),
            )
            timeline += penalty_events(kicks, result.home_name, result.away_name)
            penalties = (kicks[-1].home_score, kicks[-1].away_score)
        record_friendly(home_id, away_id, result, penalties, self.database_path)
        await context.send(f"🏟️ {title} · Local: **{venue_name(home)}**")
        await narrate(
            lambda embed: context.send(embed=embed),
            result.home_name,
            result.away_name,
            timeline,
            title=title,
            penalties=penalties,
            home_color=home.get("color"),
            away_color=away.get("color"),
        )
        await context.send(
            file=await score_file(
                home,
                away,
                result.home_goals,
                result.away_goals,
                result.goals,
                title=title,
                venue=venue_name(home),
                penalties=penalties,
            )
        )
        for user_id in (home_id, away_id):
            record_event(user_id, "friendly", self.database_path)
        return result, penalties

    async def _play_friendly(
        self, context: commands.Context, opponent: discord.Member, with_income: bool
    ) -> None:
        # Empate no amistoso também vai para os pênaltis.
        played = await self._leg(
            context,
            context.author.id,
            opponent.id,
            "Amistoso",
            lambda result: result.home_goals == result.away_goals,
        )
        if played is None:
            return
        result, penalties = played
        # Empate não existe mais no amistoso: quem ganha nos pênaltis também vence.
        home_won = result.home_goals > result.away_goals or (
            penalties is not None and penalties[0] > penalties[1]
        )
        winner = context.author.id if home_won else opponent.id
        record_event(winner, "friendly_win", self.database_path)
        if with_income:
            await self._collect_income(context, result)
        return winner

    async def _play_two_legs(
        self, context: commands.Context, opponent: discord.Member, with_income: bool
    ) -> None:
        """Ida na casa de quem desafiou, volta na casa do outro; vale o placar somado.
        Empate no agregado: pênaltis no fim da volta. A renda (se escolhida) é só a da ida."""
        first = await self._leg(
            context, context.author.id, opponent.id, "Amistoso · Ida", lambda result: False
        )
        if first is None:
            return
        ida, _ = first
        if with_income:
            await self._collect_income(context, ida)

        def tied_on_aggregate(volta) -> bool:
            return ida.home_goals + volta.away_goals == ida.away_goals + volta.home_goals

        second = await self._leg(
            context, opponent.id, context.author.id, "Amistoso · Volta", tied_on_aggregate
        )
        if second is None:
            return
        volta, penalties = second
        challenger = ida.home_goals + volta.away_goals
        rival = ida.away_goals + volta.home_goals
        if challenger != rival:
            challenger_won = challenger > rival
        else:
            challenger_won = penalties is not None and penalties[1] > penalties[0]
        winner = context.author if challenger_won else opponent
        record_event(winner.id, "friendly_win", self.database_path)
        decided = (
            f" (pênaltis {max(penalties)} a {min(penalties)})"
            if challenger == rival and penalties
            else ""
        )
        await context.send(
            f"🏆 **Agregado:** {ida.home_name} {challenger} × {rival} {ida.away_name}{decided}. "
            f"**{winner.display_name}** venceu o ida e volta!"
        )
        return winner.id

    async def play_wagered(self, context, opponent, wager: int, two_legs: bool) -> None:
        """Joga o amistoso apostado e paga o pote; se não der para jogar, devolve as apostas."""
        if two_legs:
            winner = await self._play_two_legs(context, opponent, False)
        else:
            winner = await self._play_friendly(context, opponent, False)
        if winner is None:
            for user_id in (context.author.id, opponent.id):
                pay(user_id, wager, self.database_path)
            await context.send("A partida não aconteceu: as apostas voltaram.")
            return
        pay(winner, wager * 2, self.database_path)
        name = context.author.display_name if winner == context.author.id else opponent.display_name
        await context.send(f"💰 **{name}** levou o pote de {icons.memilets(wager * 2)} Memilets.")

    async def _collect_income(self, context: commands.Context, result) -> None:
        try:
            income = collect_income(
                context.author.id, result.home_goals, result.away_goals, self.database_path
            )
        except IncomeError as error:
            await context.send(f"Sem renda neste amistoso: {error}")
            return
        await context.send(
            f"{income.message} {icons.memilets()} · próxima renda <t:{int(income.available_at.timestamp())}:R>"
        )

    @commands.group(name="campeonato", invoke_without_command=True)
    async def championship(
        self,
        context: commands.Context,
        operation: str | None = None,
        *,
        value: str | None = None,
    ) -> None:
        operation = normalize_player_name(operation) if operation else None
        if operation == "inscrever":
            if context.guild is None:
                await context.reply(
                    "O campeonato só funciona em um servidor.", mention_author=False
                )
                return
            try:
                register_team(
                    context.guild.id,
                    context.author.id,
                    database_path=self.database_path,
                )
            except (ChampionshipError, TeamError) as error:
                await context.reply(str(error), mention_author=False)
                return
            await context.reply(
                "Seu time está inscrito no campeonato de hoje. "
                "As inscrições fecham quando o campeonato for iniciado.",
                mention_author=False,
            )
            return

        if operation == "iniciar":
            if context.guild is None:
                await context.reply(
                    "O campeonato só funciona em um servidor.", mention_author=False
                )
                return
            if not context.author.guild_permissions.manage_guild:
                await context.reply(
                    "É necessário ter a permissão Gerenciar servidor para iniciar.",
                    mention_author=False,
                )
                return
            try:
                table = play_championship(
                    context.guild.id,
                    database_path=self.database_path,
                )
            except (ChampionshipError, TeamError) as error:
                await context.reply(str(error), mention_author=False)
                return
            messages = self._split_message(self._format_championship(table, include_final=False))
            await context.reply(messages[0], mention_author=False)
            for message in messages[1:]:
                await context.send(message)
            await self._narrate_final(context.send, table)
            return

        if operation == "canal":
            if context.guild is None or not isinstance(context.channel, discord.TextChannel):
                await context.reply(
                    "Configure o canal em um canal de texto do servidor.",
                    mention_author=False,
                )
                return
            if not context.author.guild_permissions.manage_guild:
                await context.reply(
                    "É necessário ter a permissão Gerenciar servidor para configurar.",
                    mention_author=False,
                )
                return
            channel = context.channel
            if value:
                channel_id = value.strip().removeprefix("<#").removesuffix(">")
                try:
                    channel = context.guild.get_channel(int(channel_id))
                except ValueError:
                    channel = None
                if not isinstance(channel, discord.TextChannel):
                    await context.reply("Canal de texto inválido.", mention_author=False)
                    return
            with connect(self.database_path) as connection:
                connection.execute(
                    """
                    INSERT INTO championship_settings (guild_id, channel_id, enabled)
                    VALUES (?, ?, 0)
                    ON CONFLICT(guild_id) DO UPDATE SET
                        channel_id = excluded.channel_id,
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    (str(context.guild.id), str(channel.id)),
                )
            await context.reply(
                f"Canal do campeonato definido: {channel.mention}. "
                "O agendamento automático ainda está desligado.",
                mention_author=False,
            )
            return

        if operation == "ativar":
            if context.guild is None or not context.author.guild_permissions.manage_guild:
                await context.reply(
                    "É necessário ter a permissão Gerenciar servidor.",
                    mention_author=False,
                )
                return
            if not self.scheduler_enabled:
                await context.reply(
                    "O agendador global está desligado. Defina "
                    "`MEMI_CHAMPIONSHIP_SCHEDULER=1` após validar a operação "
                    "e reiniciar o bot.",
                    mention_author=False,
                )
                return
            with connect(self.database_path) as connection:
                cursor = connection.execute(
                    """
                    UPDATE championship_settings SET enabled = 1,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE guild_id = ?
                    """,
                    (str(context.guild.id),),
                )
            if cursor.rowcount == 0:
                await context.reply(
                    "Configure primeiro o canal com `mm!campeonato canal`.",
                    mention_author=False,
                )
            else:
                await context.reply(
                    "Agendamento diário ativado para as 19:00 (horário de Brasília).",
                    mention_author=False,
                )
            return

        if context.guild is None:
            await context.reply("O campeonato só funciona em um servidor.", mention_author=False)
            return
        table = get_championship(
            context.guild.id,
            database_path=self.database_path,
        )
        if table is None:
            await context.reply(
                "Ainda não há campeonato hoje. Inscreva seu time com " "`mm!campeonato inscrever`.",
                mention_author=False,
            )
            return
        await context.reply(
            self._format_championship(table),
            mention_author=False,
        )

    @tasks.loop(time=time(hour=19, minute=0, tzinfo=CHAMPIONSHIP_TIMEZONE))
    async def daily_scheduler(self) -> None:
        with connect(self.database_path) as connection:
            settings = [dict(row) for row in connection.execute("""
                    SELECT guild_id, channel_id FROM championship_settings
                    WHERE enabled = 1
                    """)]
        for setting in settings:
            try:
                result = play_championship(
                    setting["guild_id"],
                    database_path=self.database_path,
                )
            except ChampionshipError as error:
                LOGGER.info(
                    "Campeonato não executado no servidor %s: %s",
                    setting["guild_id"],
                    error,
                )
                continue
            channel = self.bot.get_channel(int(setting["channel_id"]))
            if channel is None:
                try:
                    channel = await self.bot.fetch_channel(int(setting["channel_id"]))
                except discord.NotFound:
                    LOGGER.warning(
                        "Canal %s do campeonato não foi encontrado.",
                        setting["channel_id"],
                    )
                    continue
            if isinstance(channel, discord.TextChannel):
                for message in self._split_message(
                    self._format_championship(result, include_final=False)
                ):
                    await channel.send(message)
                await self._narrate_final(channel.send, result)

    @daily_scheduler.before_loop
    async def before_daily_scheduler(self) -> None:
        await self.bot.wait_until_ready()

    async def _narrate_final(self, send, table: dict) -> None:
        """Repete a final lance a lance, com pênaltis se houve empate."""
        final = next((fixture for fixture in table["fixtures"] if fixture["is_final"]), None)
        if final is None or not final.get("events_json"):
            return
        events = json.loads(final["events_json"])
        names = {team["discord_user_id"]: team["team_name"] for team in table["entries"]}
        home = names.get(final["home_user_id"], events.get("home_name", "Mandante"))
        away = names.get(final["away_user_id"], events.get("away_name", "Visitante"))
        timeline = list(events.get("events", []))
        if not timeline:
            return
        penalties = None
        if events.get("penalty_kicks"):
            timeline += penalty_events(events["penalty_kicks"], home, away)
            penalties = tuple(events["penalties"])
        home_team = get_team(final["home_user_id"], self.database_path) or {"team_name": home}
        away_team = get_team(final["away_user_id"], self.database_path) or {"team_name": away}
        await narrate(
            lambda embed: send(embed=embed),
            home,
            away,
            timeline,
            title="FINAL do campeonato",
            penalties=penalties,
            home_color=home_team.get("color"),
            away_color=away_team.get("color"),
        )
        await send(
            file=await score_file(
                home_team,
                away_team,
                int(final["home_goals"]),
                int(final["away_goals"]),
                events.get("goals", []),
                title="Final do campeonato",
                penalties=penalties,
            )
        )
        if final.get("winner_user_id"):
            winner = names.get(final["winner_user_id"], final["winner_user_id"])
            prize = (
                f" Prêmio: {icons.memilets(CHAMPION_PRIZE)} Memilets." if table.get("prize") else ""
            )
            await send(f"🏆 **{winner}** é o campeão do dia!{prize}")
        box_office = events.get("box_office") or {}
        if any(box_office.values()):
            await send(
                f"🎟️ Bilheteria da final · {home} +{box_office.get('home', 0)} · "
                f"{away} +{box_office.get('away', 0)}"
            )

    @staticmethod
    def _format_match(result) -> str:
        lines = [
            f"**{result.home_name} {result.home_goals} × "
            f"{result.away_goals} {result.away_name}**",
            (
                f"xG: {result.home_xg:.2f} × {result.away_xg:.2f} · "
                f"ataque/defesa: "
                f"{result.home_strength.attack:.1f}/{result.home_strength.resistance:.1f} "
                f"× {result.away_strength.attack:.1f}/{result.away_strength.resistance:.1f}"
            ),
        ]
        lines.extend(
            f"`{goal.minute:02d}'` ⚽ {goal.scorer_name}{SET_PIECE_MARK.get(goal.play, '')} "
            f"({'mandante' if goal.side == 'home' else 'visitante'})"
            for goal in result.goals
        )
        return "\n".join(lines)

    @staticmethod
    def _format_championship(table: dict, *, include_final: bool = True) -> str:
        names = {team["discord_user_id"]: team["team_name"] for team in table["entries"]}
        lines = [
            f"**{index}. {team['team_name']}** — "
            f"{team['points']} pts · {team['wins']}V "
            f"{team['draws']}E {team['losses']}D · "
            f"SG {team['gf'] - team['ga']}"
            for index, team in enumerate(table["standings"], start=1)
        ]
        if not lines:
            lines = ["Nenhum time inscrito."]
        group_fixtures = [fixture for fixture in table["fixtures"] if not fixture["is_final"]]
        if group_fixtures:
            lines.extend(["", "**Jogos da fase todos-contra-todos:**"])
            lines.extend(
                (
                    f"{names.get(fixture['home_user_id'], fixture['home_user_id'])} "
                    f"{fixture['home_goals'] if fixture['home_goals'] is not None else '—'} "
                    f"× {fixture['away_goals'] if fixture['away_goals'] is not None else '—'} "
                    f"{names.get(fixture['away_user_id'], fixture['away_user_id'])}"
                    + MatchesCog._box_office_text(fixture)
                )
                for fixture in group_fixtures
            )
        final = next(
            (fixture for fixture in table["fixtures"] if fixture["is_final"]),
            None,
        )
        if final and final["home_goals"] is not None and not include_final:
            home = names.get(final["home_user_id"], final["home_user_id"])
            away = names.get(final["away_user_id"], final["away_user_id"])
            lines.extend(["", f"**Final:** {home} × {away} — começa agora! 🔴"])
        elif final and final["home_goals"] is not None:
            home = names.get(final["home_user_id"], final["home_user_id"])
            away = names.get(final["away_user_id"], final["away_user_id"])
            lines.extend(
                [
                    "",
                    f"**Final:** {home} {final['home_goals']} × " f"{final['away_goals']} {away}",
                ]
            )
            if final["winner_user_id"]:
                winner = names.get(final["winner_user_id"], final["winner_user_id"])
                lines.append(f"🏆 Campeão: **{winner}**")
        return "**Campeonato diário**\n" + "\n".join(lines)

    @staticmethod
    def _box_office_text(fixture: dict) -> str:
        """Resumo da bilheteria (andar 5) dos dois times, quando alguém ganhou algo."""
        try:
            box_office = json.loads(fixture.get("events_json") or "{}").get("box_office") or {}
        except ValueError:
            return ""
        if not any(box_office.values()):
            return ""
        return f" · 🎟️ +{box_office.get('home', 0)} / +{box_office.get('away', 0)}"

    @staticmethod
    def _split_message(content: str, limit: int = 1_900) -> list[str]:
        messages = []
        current = ""
        for line in content.splitlines():
            candidate = f"{current}\n{line}" if current else line
            if len(candidate) > limit and current:
                messages.append(current)
                current = line
            else:
                current = candidate
        if current:
            messages.append(current)
        return messages


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(MatchesCog(bot))
