"""Comandos de diagnóstico restritos ao proprietário do bot."""

from __future__ import annotations

import asyncio
import random
from io import BytesIO

import discord
from discord.ext import commands

from src.admins import is_admin
from src.db import DATABASE_PATH
from src.dedupe import find_duplicates, remove_duplicates
from src.narration import narrate
from src.simulation import penalty_events, penalty_shootout_kicks, simulate_match

TEST_RATINGS = [80, 75, 90, 77, 60, 50]


# Nomes de mentira para os jogos de teste (cada time de teste pega 11 diferentes).
TEST_FIRST = (
    "Rafael",
    "Bruno",
    "Diego",
    "Thiago",
    "Lucas",
    "Matheus",
    "Caio",
    "Igor",
    "Vitor",
    "Danilo",
    "Renan",
    "Felipe",
    "Gustavo",
    "André",
    "Hugo",
    "Otávio",
)
TEST_LAST = (
    "Moura",
    "Teixeira",
    "Ramos",
    "Pires",
    "Farias",
    "Nogueira",
    "Castro",
    "Lopes",
    "Barros",
    "Cardoso",
    "Rezende",
    "Matos",
    "Freitas",
    "Aguiar",
    "Siqueira",
    "Vidal",
)
TEST_COLORS = ("#C8102E", "#1E5AA8", "#1F7A3A", "#E8B84A", "#7B3FB0", "#333333")


def _test_lineup(overall: int, prefix: str) -> list[dict]:
    positions = ["GOL", "ZAG", "ZAG", "LE", "LD", "MC", "MC", "MEI", "PE", "PD", "CA"]
    names = random.Random(prefix).sample(
        [f"{first} {last}" for first in TEST_FIRST for last in TEST_LAST], len(positions)
    )
    return [
        {
            "id": index + 1,
            "name": names[index],
            "display_name": names[index],
            "position": position,
            "overall": overall,
        }
        for index, position in enumerate(positions)
    ]


def _test_color(name: str) -> str:
    return TEST_COLORS[sum(map(ord, name)) % len(TEST_COLORS)]


async def _play_final(send, home: tuple[str, int], away: tuple[str, int], rng, title: str):
    """Joga uma partida narrada; empate vai para os pênaltis."""
    home_players = _test_lineup(home[1], home[0])
    away_players = _test_lineup(away[1], away[0])
    result = simulate_match(home[0], home_players, away[0], away_players, rng=rng)
    timeline = list(result.events)
    penalties = None
    if result.home_goals == result.away_goals:
        kicks = penalty_shootout_kicks(home_players, away_players, rng=rng)
        timeline += penalty_events(kicks, home[0], away[0])
        penalties = (kicks[-1].home_score, kicks[-1].away_score)
    home_color, away_color = _test_color(home[0]), _test_color(away[0])
    if home_color == away_color:
        away_color = TEST_COLORS[(TEST_COLORS.index(home_color) + 1) % len(TEST_COLORS)]
    await narrate(
        send,
        home[0],
        away[0],
        timeline,
        title=title,
        penalties=penalties,
        home_color=home_color,
        away_color=away_color,
    )
    if penalties:
        return home[0] if penalties[0] > penalties[1] else away[0]
    return home[0] if result.home_goals > result.away_goals else away[0]


class ConfirmDedupeView(discord.ui.View):
    def __init__(self, owner_id: int, database_path):
        super().__init__(timeout=120)
        self.owner_id = owner_id
        self.database_path = database_path

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.owner_id

    @discord.ui.button(label="Remover duplicados", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button) -> None:
        report = await asyncio.to_thread(remove_duplicates, self.database_path)
        await interaction.response.edit_message(
            content=(
                f"✅ {report['removed']} duplicados removidos, "
                f"{report['transferred']} posses/wishes transferidas, "
                f"{report['skipped']} grupos mantidos por conflito de dono."
            ),
            view=None,
        )
        self.stop()

    @discord.ui.button(label="Cancelar", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button) -> None:
        await interaction.response.edit_message(content="Nada foi alterado.", view=None)
        self.stop()


class AdminCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.database_path = getattr(bot, "database_path", DATABASE_PATH)

    async def cog_check(self, context: commands.Context) -> bool:
        if not is_admin(context.author.id):
            await context.reply(
                "Este comando é exclusivo da conta administrativa do MEMI.", mention_author=False
            )
            return False
        return True

    @commands.group(name="admin", invoke_without_command=True, case_insensitive=True)
    async def admin(self, context: commands.Context) -> None:
        embed = discord.Embed(
            title="MEMI Admin",
            description=(
                "`mm!testeamistoso` — partida de teste narrada (empate vai aos pênaltis)\n"
                "`mm!testecampeonato <n>` — campeonato de teste com final narrada\n"
                "`mm!testeimagem` — a escalação em embed, anexo e Components V2\n"
                "`mm!admin duplicados` — procura e remove jogadores duplicados"
            ),
            color=discord.Color.red(),
        )
        await context.reply(embed=embed, mention_author=False)

    @admin.command(name="testeamistoso")
    async def test_friendly(self, context: commands.Context) -> None:
        winner = await _play_final(
            lambda embed: context.reply(embed=embed, mention_author=False),
            ("Teste 80", 80),
            ("Teste 75", 75),
            random.SystemRandom(),
            "Amistoso de teste",
        )
        await context.send(f"🏆 Vencedor do teste: **{winner}**")

    @admin.command(name="testecampeonato")
    async def test_championship(self, context: commands.Context, teams: int = 6) -> None:
        if not 2 <= teams <= 30:
            await context.reply("Informe entre 2 e 30 times.", mention_author=False)
            return
        rng = random.SystemRandom()
        ratings = {
            f"Teste {index + 1}": TEST_RATINGS[index % len(TEST_RATINGS)] for index in range(teams)
        }
        table = {name: {"pts": 0, "gf": 0, "ga": 0} for name in ratings}
        names = list(ratings)
        results = []
        for home_index, home in enumerate(names):
            for away in names[home_index + 1 :]:
                result = simulate_match(
                    home,
                    _test_lineup(ratings[home], "H"),
                    away,
                    _test_lineup(ratings[away], "A"),
                    rng=rng,
                )
                results.append(f"{home} {result.home_goals} × {result.away_goals} {away}")
                for name, scored, conceded in (
                    (home, result.home_goals, result.away_goals),
                    (away, result.away_goals, result.home_goals),
                ):
                    table[name]["gf"] += scored
                    table[name]["ga"] += conceded
                    table[name]["pts"] += (
                        3 if scored > conceded else (1 if scored == conceded else 0)
                    )
        ranking = sorted(
            table.items(),
            key=lambda item: (
                -item[1]["pts"],
                -(item[1]["gf"] - item[1]["ga"]),
                -item[1]["gf"],
                item[0],
            ),
        )
        description = "\n".join(
            f"`{index}.` {name} ({ratings[name]}) — **{row['pts']} pts** · SG {row['gf'] - row['ga']}"
            for index, (name, row) in enumerate(ranking, 1)
        )
        embed = discord.Embed(
            title=f"Teste de campeonato ({teams} times)",
            description=description[:4000],
            color=discord.Color.red(),
        )
        if len(results) <= 15:
            embed.add_field(name="Jogos", value="\n".join(results)[:1024], inline=False)
        embed.set_footer(text="A final entre os dois primeiros começa agora.")
        await context.reply(embed=embed, mention_author=False)
        (first, _), (second, _) = ranking[0], ranking[1]
        winner = await _play_final(
            lambda final_embed: context.send(embed=final_embed),
            (first, ratings[first]),
            (second, ratings[second]),
            rng,
            "FINAL de teste",
        )
        await context.send(f"🏆 Campeão do teste: **{winner}**")

    @commands.command(name="testeamistoso", hidden=True)
    async def test_friendly_shortcut(self, context: commands.Context) -> None:
        await self.test_friendly(context)

    @commands.command(name="testecampeonato", hidden=True)
    async def test_championship_shortcut(self, context: commands.Context, teams: int = 6) -> None:
        await self.test_championship(context, teams)

    @admin.command(name="testeimagem")
    async def test_image(self, context: commands.Context) -> None:
        """Manda a mesma escalação de três jeitos, para comparar o tamanho no Discord."""
        from src.squad_image import render_squad
        from src.teams import get_lineup, get_team

        team = get_team(context.author.id, self.database_path)
        lineup = get_lineup(context.author.id, self.database_path) if team else []
        if team is None:
            team = {"team_name": "Time de teste", "color": "#2E8B57", "formation": "4-3-3"}
        image = (await asyncio.to_thread(render_squad, team, lineup)).getvalue()

        def file() -> discord.File:
            return discord.File(BytesIO(image), filename="escalacao.png")

        embed = discord.Embed(title="1 · Dentro de embed", color=discord.Color.blurple())
        embed.set_image(url="attachment://escalacao.png")
        await context.send(embed=embed, file=file())
        await context.send("**2 · Anexo solto** (sem embed)", file=file())
        layout = discord.ui.LayoutView()
        layout.add_item(
            discord.ui.Container(
                discord.ui.TextDisplay("**3 · Components V2** (galeria de mídia)"),
                discord.ui.MediaGallery(discord.MediaGalleryItem("attachment://escalacao.png")),
            )
        )
        await context.send(view=layout, file=file())

    @commands.command(name="testeimagem", hidden=True)
    async def test_image_shortcut(self, context: commands.Context) -> None:
        await self.test_image(context)

    @admin.command(name="duplicados", aliases=["duplicatas"])
    async def duplicates(self, context: commands.Context) -> None:
        groups = await asyncio.to_thread(find_duplicates, self.database_path)
        if not groups:
            await context.reply("Nenhum jogador duplicado encontrado. ✅", mention_author=False)
            return
        lines = []
        for group in groups[:15]:
            keep, *drop = group["players"]
            lines.append(
                f"**{keep['name']}** — mantém ID {keep['id']} ({keep['overall']}), remove "
                + ", ".join(f"ID {player['id']} ({player['overall']})" for player in drop)
                + (" ⚠️ conflito de dono" if group["conflict"] else "")
            )
        embed = discord.Embed(
            title=f"{len(groups)} grupos de duplicados",
            description="\n".join(lines)[:4000],
            color=discord.Color.orange(),
        )
        embed.set_footer(
            text="Mantém o de maior overall; posse, bolas e wishes passam para ele. "
            "Grupos com donos diferentes não são alterados."
        )
        await context.reply(
            embed=embed,
            view=ConfirmDedupeView(context.author.id, self.database_path),
            mention_author=False,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AdminCog(bot))
