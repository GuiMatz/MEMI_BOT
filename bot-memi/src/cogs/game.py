"""Comandos iniciais de consulta e conta do jogo."""

from __future__ import annotations

import re

import discord
from discord.ext import commands

from src.db import DATABASE_PATH
from src.game import (
    get_catalog_entries,
    get_collection_left,
    get_top_players,
    get_wallet,
    list_players_by_team_or_series,
    list_owned_players,
    register_user,
)


class TeamPlayersView(discord.ui.View):
    def __init__(self, pages: list[list[dict]], query: str):
        super().__init__(timeout=180)
        self.pages = pages
        self.query = query
        self.index = 0
        self.previous.disabled = True
        self.next.disabled = len(pages) <= 1

    def embed(self) -> discord.Embed:
        page = self.pages[self.index]
        lines = [
            f"`{index:>2}.` **{player['overall']}** — "
            f"{player['name']} ({player['game_position'] or '—'})"
            for index, player in enumerate(
                page, start=self.index * 20 + 1
            )
        ]
        embed = discord.Embed(
            title=f"Jogadores: {self.query}",
            description="\n".join(lines),
            color=discord.Color.gold(),
        )
        embed.set_footer(text=f"Página {self.index + 1}/{len(self.pages)}")
        return embed

    async def refresh(self, interaction: discord.Interaction) -> None:
        self.previous.disabled = self.index == 0
        self.next.disabled = self.index == len(self.pages) - 1
        await interaction.response.edit_message(embed=self.embed(), view=self)

    @discord.ui.button(label="◀", style=discord.ButtonStyle.secondary)
    async def previous(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        self.index -= 1
        await self.refresh(interaction)

    @discord.ui.button(label="▶", style=discord.ButtonStyle.secondary)
    async def next(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        self.index += 1
        await self.refresh(interaction)


class CatalogView(discord.ui.View):
    def __init__(self, pages: list[list[dict]], title: str):
        super().__init__(timeout=300)
        self.pages = pages
        self.title = title
        self.index = 0
        self.previous.disabled = True
        self.next.disabled = len(pages) <= 1

    def embed(self) -> discord.Embed:
        lines = []
        for number, entry in enumerate(self.pages[self.index], self.index * 20 + 1):
            if "overall" in entry:
                lines.append(
                    f"`{number:>3}.` **{entry['overall']}** — "
                    f"{entry['name']} ({entry['game_position']})"
                )
            else:
                lines.append(f"`{number:>3}.` {entry['name']}")
        embed = discord.Embed(
            title=self.title,
            description="\n".join(lines) or "Nenhum item encontrado.",
            color=discord.Color.gold(),
        )
        embed.set_footer(text=f"Página {self.index + 1}/{len(self.pages)}")
        return embed

    async def refresh(self, interaction: discord.Interaction) -> None:
        self.previous.disabled = self.index == 0
        self.next.disabled = self.index == len(self.pages) - 1
        await interaction.response.edit_message(embed=self.embed(), view=self)

    @discord.ui.button(label="◀", style=discord.ButtonStyle.secondary)
    async def previous(self, interaction, button) -> None:
        self.index -= 1
        await self.refresh(interaction)

    @discord.ui.button(label="▶", style=discord.ButtonStyle.secondary)
    async def next(self, interaction, button) -> None:
        self.index += 1
        await self.refresh(interaction)


class GameCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.database_path = getattr(bot, "database_path", DATABASE_PATH)

    @commands.Cog.listener()
    async def on_command(self, context: commands.Context) -> None:
        if context.author.bot:
            return
        register_user(
            context.author.id,
            context.author.display_name,
            self.database_path,
        )

    @commands.command(name="futae", aliases=["fut.ae"])
    async def help_command(self, context: commands.Context) -> None:
        embed = discord.Embed(
            title="Bot Memi — comandos",
            description="Use o prefixo `mm!`.",
            color=discord.Color.blurple(),
        )
        embed.add_field(
            name="Disponíveis",
            value=(
                "`mm!fut.ae` — mostra esta lista\n"
                "`mm!scout <nome>` — procura um jogador\n"
                "`mm!top [categoria]` — consulta o catálogo\n"
                "`mm!roll` — rola um jogador e abre um claim\n"
                "`mm!daily` — resgata sua recompensa diária\n"
                "`mm!upgrade` — consulta ou compra upgrades\n"
                "`mm!crates` — abre uma crate\n"
                "`mm!wish`, `mm!wishlist`/`mm!wl`, `mm!starwish`/`mm!sw` — wishes\n"
                "`mm!elenco [@pessoa]` — consulta um elenco\n"
                "`mm!time`, `mm!escalar`, `mm!tatica` — gerencia seu time\n"
                "`mm!troca` / `mm!demitir` — negocia ou libera jogadores\n"
                "`mm!left` — mostra jogadores, uniformes e logos disponíveis\n"
                "`mm!amistoso @pessoa` — simula uma partida de teste\n"
                "`mm!carteira` — consulta seu saldo"
            ),
            inline=False,
        )
        embed.set_footer(text="Use mm!futae para ver as funções do jogo.")
        await context.reply(embed=embed, mention_author=False)

    @commands.command(name="left")
    async def collection_left(self, context: commands.Context) -> None:
        counts = get_collection_left(self.database_path)
        await context.reply(
            "📦 Ainda disponíveis:\n"
            f"⚽ Jogadores: **{counts['jogadores']}**\n"
            f"👕 Uniformes: **{counts['uniformes']}**\n"
            f"🛡️ Logos: **{counts['logos']}**",
            mention_author=False,
        )

    @commands.command(name="scout")
    async def player_info(
        self,
        context: commands.Context,
        *,
        query: str,
    ) -> None:
        from src.game import search_players

        players = search_players(query, self.database_path)
        if not players:
            await context.reply(
                "Não encontrei jogador com esse nome.",
                mention_author=False,
            )
            return

        if len(players) > 1:
            choices = "\n".join(
                f"`{index}.` {player['display_name'] or player['name']} — "
                f"overall **{player['overall'] or '—'}**, "
                f"{player['game_position'] or 'posição —'} · "
                f"{player['overall_edition'] or player['overall_source'] or 'série não informada'} "
                f"(ID {player['id']})"
                for index, player in enumerate(players, start=1)
            )
            await context.reply(
                f"Encontrei mais de um resultado:\n{choices}",
                mention_author=False,
            )
            return

        player = players[0]
        embed = discord.Embed(
            title=player["display_name"] or player["name"],
            color=discord.Color.blurple(),
        )
        embed.add_field(
            name="Posição",
            value=player["game_position"] or "Não definida",
            inline=True,
        )
        embed.add_field(
            name="Overall",
            value=str(player["overall"] or "Não definido"),
            inline=True,
        )
        embed.set_footer(text=f"ID {player['id']} • dados locais do MEMI")
        await context.reply(embed=embed, mention_author=False)

    @commands.command(name="top")
    async def top_players(
        self,
        context: commands.Context,
        category: str = "jogadores",
    ) -> None:
        entries, total = get_catalog_entries(category, self.database_path, limit=500)
        if not entries:
            await context.reply(
                "Categoria inválida ou sem itens. Use jogadores, técnicos, uniformes, "
                "mascotes, estadios, ligas, cartola ou emblemas.",
                mention_author=False,
            )
            return
        pages = [entries[index:index + 20] for index in range(0, len(entries), 20)]
        view = CatalogView(pages, f"TOP {category.upper()} ({total})")
        await context.reply(embed=view.embed(), view=view, mention_author=False)

    @commands.command(name="elenco")
    async def roster(
        self,
        context: commands.Context,
        *,
        query: str | None = None,
    ) -> None:
        target = context.message.mentions[0] if context.message.mentions else context.author
        filter_text = re.sub(r"<@!?\d+>", "", query or "").strip()
        register_user(target.id, target.display_name, self.database_path)
        filters: dict[str, object] = {}
        if filter_text:
            position = re.fullmatch(r"(?:posicao|posição)\s+([A-Za-z]{2,3})", filter_text, re.I)
            overall = re.fullmatch(
                r"(?:overall\s*)?(>=|<=|>|<|=)?\s*(\d{1,2})",
                filter_text,
                re.I,
            )
            category = re.fullmatch(r"(clube|liga)\s+(.+)", filter_text, re.I)
            if position:
                filters["position"] = position.group(1).upper()
            elif overall:
                operator = overall.group(1) or "="
                rating = int(overall.group(2))
                if operator == ">=":
                    filters["min_overall"] = rating
                elif operator == ">":
                    filters["min_overall"] = min(99, rating + 1)
                elif operator == "<=":
                    filters["max_overall"] = rating
                elif operator == "<":
                    filters["max_overall"] = max(1, rating - 1)
                else:
                    filters["min_overall"] = rating
                    filters["max_overall"] = rating
            elif category:
                filters["club_query" if category.group(1).casefold() == "clube" else "league_query"] = category.group(2)
            else:
                await context.reply(
                    "Filtro inválido. Use `posicao CA`, `overall >= 80`, "
                    "`clube nome` ou `liga nome`.",
                    mention_author=False,
                )
                return
        try:
            players = list_owned_players(
                target.id,
                self.database_path,
                limit=20,
                **filters,
            )
        except ValueError as error:
            await context.reply(str(error), mention_author=False)
            return
        if not players:
            await context.reply(
                f"Nenhum jogador encontrado no elenco de {target.display_name}.",
                mention_author=False,
            )
            return
        lines = [
            f"`{index:>2}.` **{player['overall'] or '—'}** — {player['name']} "
            f"({player['game_position'] or 'posição pendente'}) · ID `{player['id']}`"
            for index, player in enumerate(players, start=1)
        ]
        embed = discord.Embed(
            title=f"Elenco de {target.display_name}",
            description="\n".join(lines),
            color=discord.Color.green(),
        )
        if filter_text:
            embed.set_footer(text=f"Filtro: {filter_text}")
        await context.reply(embed=embed, mention_author=False)

    @commands.command(name="carteira")
    async def wallet(self, context: commands.Context) -> None:
        account = get_wallet(context.author.id, self.database_path)
        balance = f"{account['memilets']:,}".replace(",", ".")
        await context.reply(
            f"**{account['display_name']}** tem **{balance} Memilets**.",
            mention_author=False,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(GameCog(bot))
