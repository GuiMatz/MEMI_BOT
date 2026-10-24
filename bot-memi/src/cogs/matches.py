"""Comandos de amistoso, desafios, estatísticas e comparação de times."""

from __future__ import annotations

import asyncio
import logging

import discord
from discord.ext import commands

from src import icons
from src.cogs.penalties import PenaltyChallengeView
from src.wagers import WagerError, check_wager, parse_wager, pay, take_stakes
from src.db import DATABASE_PATH, connect, normalize_player_name
from src.income import IncomeError, collect_income, income_status, venue_name
from src.missions import record_event
from src.narration import narrate
from src.positions import slot_rating
from src.pve import PveError, opponent_for, pve_title, record_result
from src.pve_crests import crest_for
from src.commentary import rivalry_intro, rivalry_line
from src.compare import compare_image, compare_rows, forecast, head_to_head, snapshot
from src.cogs.pve_views import PveHubView, hub_embed
from src.score_image import render_score_gif
from src.simulation import penalty_events, penalty_shootout_kicks, simulate_match
from src.team_stats import (
    KINDS,
    PERIODS,
    compute_stats,
    filter_records,
    match_records,
    record_friendly,
    rivalry,
)
from src.teams import STYLE_LABELS, TeamError, get_team, get_valid_lineup

LOGGER = logging.getLogger("memi.matches")


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


FORM_EMOJIS = {"V": "🟩", "E": "⬜", "D": "🟥"}


def compare_embed(mine, theirs, odds, record) -> discord.Embed:
    """A tabela lado a lado (▲ em quem é melhor), destaques, confronto e prognóstico."""
    left, right = mine.name[:14], theirs.name[:14]
    lines = [f"{'':<9}{left:>14}   {right:<14}"]
    for label, first, second in compare_rows(mine, theirs):
        mark_left = "▲" if first > second else " "
        mark_right = "▲" if second > first else " "
        lines.append(f"{label:<9}{first:>12} {mark_left}   {mark_right} {second:<12}")
    embed = discord.Embed(
        title=f"⚔️ {mine.name} × {theirs.name}",
        description="```\n" + "\n".join(lines) + "\n```",
        color=discord.Color.from_rgb(230, 181, 74),
    )
    for snap in (mine, theirs):
        best = snap.best
        best_text = (
            f"{best.get('display_name') or best['name']} ({best['effective_overall']})"
            if best
            else "—"
        )
        form = "".join(FORM_EMOJIS.get(outcome, "") for outcome in snap.form) or "sem jogos"
        embed.add_field(
            name=snap.name[:100],
            value=(
                f"{snap.team.get('formation') or '—'} · {STYLE_LABELS.get(snap.team.get('style'), '—')}\n"
                f"⭐ {best_text}\n"
                f"💰 Elenco vale {icons.memilets(snap.squad_value)}\n"
                f"Forma: {form}" + ("" if snap.complete else "\n⚠️ Escalação incompleta")
            ),
            inline=True,
        )
    wins, draws, losses, goals_for, goals_against = record
    embed.add_field(
        name="🤝 Confronto direto",
        value=(
            f"{wins}V {draws}E {losses}D · gols {goals_for} × {goals_against}"
            if wins + draws + losses
            else "Ainda não se enfrentaram."
        ),
        inline=False,
    )
    if odds:
        win, draw, loss = (round(value * 100) for value in odds)
        embed.add_field(
            name="🔮 Prognóstico (você em casa)",
            value=f"{mine.name}: **{win}%** · empate **{draw}%** · {theirs.name}: **{loss}%**",
            inline=False,
        )
    else:
        embed.add_field(
            name="🔮 Prognóstico",
            value="Os dois times precisam estar com a escalação completa.",
            inline=False,
        )
    embed.set_image(url="attachment://comparar.png")
    return embed


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

    @commands.command(name="comparar", aliases=["vs", "versus", "compare"])
    async def compare(self, context: commands.Context, opponent: discord.Member) -> None:
        """`mm!comparar @pessoa`: o seu time e o da pessoa lado a lado."""
        if opponent.id == context.author.id:
            await context.reply("Compare com o time de outra pessoa.", mention_author=False)
            return
        try:
            mine = await asyncio.to_thread(snapshot, context.author.id, self.database_path)
            theirs = await asyncio.to_thread(snapshot, opponent.id, self.database_path)
        except TeamError as error:
            await context.reply(str(error), mention_author=False)
            return
        odds, record, image = await asyncio.gather(
            asyncio.to_thread(forecast, mine, theirs),
            asyncio.to_thread(head_to_head, context.author.id, opponent.id, self.database_path),
            asyncio.to_thread(compare_image, mine, theirs),
        )
        await context.reply(
            embed=compare_embed(mine, theirs, odds, record),
            file=discord.File(image, filename="comparar.png"),
            mention_author=False,
        )

    @commands.command(name="desafio", aliases=["desafios", "pve", "lendas", "chefao", "chefão"])
    async def pve_hub(self, context: commands.Context) -> None:
        """`mm!desafio`: escada, chefões e desafio livre contra times lendários."""
        try:
            _, players = get_valid_lineup(context.author.id, self.database_path)
            strength = round(
                sum(
                    slot_rating(p["effective_overall"], p["natural_position"], p["position"])
                    for p in players
                )
                / len(players)
            )
        except TeamError:
            strength = None
        await context.reply(
            embed=hub_embed(context.author, self.database_path, strength),
            view=PveHubView(self, context.author, self.database_path),
            mention_author=False,
        )

    async def start_pve(self, interaction: discord.Interaction, kind: str, team_key) -> None:
        """Um clique no painel: confere se dá para jogar, narra a partida e paga o prêmio."""
        user = interaction.user
        try:
            team = opponent_for(user.id, kind, team_key, self.database_path)
            get_valid_lineup(user.id, self.database_path)
        except (PveError, TeamError) as error:
            await interaction.response.send_message(str(error), ephemeral=True)
            return
        title = pve_title(user.id, kind, self.database_path)
        await interaction.response.send_message(
            f"⚔️ **{user.display_name}** × {team.label} (força {team.power}) · {title}"
        )
        send = interaction.channel.send
        outcome = await self._pve_match(send, user, team, title)
        if outcome is None:
            return
        won = outcome == "vitoria"
        gains = await asyncio.to_thread(
            record_result, user.id, user.display_name, kind, team, won, self.database_path
        )
        if won:
            reward = "\n".join(gains) if gains else "sem prêmio (já ganho hoje)"
            await send(f"🏆 **{user.display_name}** venceu {team.label}!\n{reward}")
        else:
            extra = ("\n" + "\n".join(gains)) if gains else ""
            first = (
                f"🤝 Empate com {team.label}: contra lendas, só a vitória conta."
                if outcome == "empate"
                else f"💥 {team.label} venceu desta vez. Tente de novo!"
            )
            await send(first + extra)

    async def _pve_match(self, send, user, team, title: str) -> str | None:
        """Partida narrada do time da pessoa (mandante) contra um time lendário.

        Devolve "vitoria", "empate" ou "derrota" (None se o time da pessoa ficou inválido).
        Contra lendas não tem pênaltis: só a vitória conta."""
        try:
            home, home_players = get_valid_lineup(user.id, self.database_path)
        except TeamError as error:
            await send(str(error))
            return None
        away = team.as_team()
        # Escudo real (clube, federação ou o da Seleção Mundial); sem ele, o gerado de sempre.
        try:
            away["logo_path"] = await asyncio.to_thread(crest_for, team.key, self.database_path)
        except Exception:
            away["logo_path"] = None
        result = simulate_match(
            home["team_name"],
            home_players,
            away["team_name"],
            list(team.players),
            home_style=home["style"],
            away_style=team.style,
            home_tactics=home,
            away_tactics=away,
            home_advantage=True,
        )
        await narrate(
            lambda embed: send(embed=embed),
            result.home_name,
            result.away_name,
            list(result.events),
            title=title,
            home_color=home.get("color"),
        )
        await send(
            file=await score_file(
                home,
                away,
                result.home_goals,
                result.away_goals,
                result.goals,
                title=title,
                venue=venue_name(home),
            )
        )
        if result.home_goals > result.away_goals:
            return "vitoria"
        return "empate" if result.home_goals == result.away_goals else "derrota"

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
        penalties, kicks = None, []
        if shootout(result):
            kicks = penalty_shootout_kicks(
                home_players,
                away_players,
                home_taker_id=home.get("penalty_player_id"),
                away_taker_id=away.get("penalty_player_id"),
            )
            timeline += penalty_events(kicks, result.home_name, result.away_name)
            penalties = (kicks[-1].home_score, kicks[-1].away_score)
        names = (result.home_name, result.away_name)
        before = await asyncio.to_thread(rivalry, home_id, away_id, self.database_path)
        record_friendly(home_id, away_id, result, penalties, self.database_path)
        after = await asyncio.to_thread(rivalry, home_id, away_id, self.database_path)
        note = rivalry_line(after, *names)
        await context.send(
            f"🏟️ {title} · Local: **{venue_name(home)}**\n{rivalry_intro(before, *names)}"
        )
        await narrate(
            lambda embed: context.send(embed=embed),
            result.home_name,
            result.away_name,
            timeline,
            title=title,
            penalties=penalties,
            home_color=home.get("color"),
            away_color=away.get("color"),
            notes=[note] if note else (),
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
                kicks=kicks,
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


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(MatchesCog(bot))
