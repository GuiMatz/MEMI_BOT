"""Comandos de diagnóstico restritos ao proprietário do bot."""

from __future__ import annotations

import os
import random

import discord
from discord.ext import commands

from src.simulation import simulate_match

DEFAULT_OWNER_ID = 277898482960826369


def _test_lineup(overall: int, prefix: str) -> list[dict]:
    positions = ["GOL", "ZAG", "ZAG", "LE", "LD", "MC", "MC", "MEI", "PE", "PD", "CA"]
    return [
        {"id": index + 1, "name": f"{prefix} {position}", "display_name": f"{prefix} {position}",
         "position": position, "overall": overall}
        for index, position in enumerate(positions)
    ]


class AdminCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.owner_id = int(os.getenv("MEMI_OWNER_ID", DEFAULT_OWNER_ID))

    async def cog_check(self, context: commands.Context) -> bool:
        if context.author.id != self.owner_id:
            await context.reply("Este comando é exclusivo da conta administrativa do MEMI.",
                                mention_author=False)
            return False
        return True

    @commands.group(name="admin", invoke_without_command=True)
    async def admin(self, context: commands.Context) -> None:
        embed = discord.Embed(
            title="MEMI Admin",
            description="Use `mm!admin testeamistoso` ou `mm!admin testecampeonato <n>`.",
            color=discord.Color.red(),
        )
        await context.reply(embed=embed, mention_author=False)

    @admin.command(name="testeamistoso")
    async def test_friendly(self, context: commands.Context) -> None:
        result = simulate_match(
            "Teste 80", _test_lineup(80, "80"),
            "Teste 75", _test_lineup(75, "75"),
            rng=random.Random(80),
        )
        embed = discord.Embed(
            title="Teste de amistoso",
            description=f"**{result.home_name} {result.home_goals} x {result.away_goals} {result.away_name}**",
            color=discord.Color.green(),
        )
        embed.add_field(name="xG", value=f"{result.home_xg:.2f} x {result.away_xg:.2f}")
        await context.reply(embed=embed, mention_author=False)

    @admin.command(name="testecampeonato")
    async def test_championship(self, context: commands.Context, teams: int = 6) -> None:
        if not 2 <= teams <= 30:
            await context.reply("Informe entre 2 e 30 times.", mention_author=False)
            return
        ratings = [80, 75, 90, 77, 60, 50]
        rng = random.Random(teams)
        scores = {f"Teste {index + 1}": 0 for index in range(teams)}
        for home_index in range(teams):
            for away_index in range(home_index + 1, teams):
                home_rating = ratings[home_index % len(ratings)]
                away_rating = ratings[away_index % len(ratings)]
                result = simulate_match(
                    f"Teste {home_index + 1}", _test_lineup(home_rating, "H"),
                    f"Teste {away_index + 1}", _test_lineup(away_rating, "A"),
                    rng=rng,
                )
                if result.home_goals > result.away_goals:
                    scores[f"Teste {home_index + 1}"] += 3
                elif result.home_goals < result.away_goals:
                    scores[f"Teste {away_index + 1}"] += 3
                else:
                    scores[f"Teste {home_index + 1}"] += 1
                    scores[f"Teste {away_index + 1}"] += 1
        ranking = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
        description = "\n".join(
            f"`{index}.` {name} — **{points} pts**"
            for index, (name, points) in enumerate(ranking, 1)
        )
        embed = discord.Embed(title=f"Teste de campeonato ({teams} times)",
                              description=description, color=discord.Color.red())
        await context.reply(embed=embed, mention_author=False)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AdminCog(bot))
