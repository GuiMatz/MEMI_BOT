"""Comandos iniciais de consulta e conta do jogo."""

from __future__ import annotations

import re
from datetime import datetime

import discord
from discord.ext import commands

from src.db import DATABASE_PATH
from src.economy import get_account_status
from src.cogs.common import CardPagerView, card_embed, card_file
from src.game import (
    find_player,
    get_catalog_entries,
    get_collection_left,
    list_owned_players,
    register_user,
    search_catalog,
)
from src.roll_images import find_image, image_file

PAGE_SIZE = 20
TOP_CATEGORIES = (
    "jogadores, cartola, tecnicos, estadios, ligas, clubes, uniformes, emblemas, "
    "logos ou mascotes"
)


def _thousands(value: int) -> str:
    return f"{value:,}".replace(",", ".")


def _timestamp(moment: datetime | None, ready: str = "**disponível agora**") -> str:
    """Horário relativo do Discord (ex.: "em 12 minutos") ou o texto de pronto."""
    if moment is None:
        return ready
    unix = int(moment.timestamp())
    return f"<t:{unix}:R> (<t:{unix}:t>)"


async def player_embed(player: dict) -> tuple[discord.Embed, discord.File]:
    """Ficha do jogador com a carta vertical (foto grande) e o dono, se houver."""
    return card_embed(player, footer="mm!wish <nome> para desejar"), await card_file(player)


def catalog_embed(item: dict) -> tuple[discord.Embed, discord.File | None]:
    """Ficha de técnico, estádio, clube, liga ou asset, com imagem se houver."""
    embed = discord.Embed(
        title=item["name"],
        description=f"**{item['kind']}**",
        color=discord.Color.teal(),
    )
    path = find_image(item.get("image_path"), item.get("entity"), item.get("id"))
    if path is None:
        embed.set_footer(text="Sem imagem local para este item.")
        return embed, None
    file = image_file(path, "item")
    embed.set_image(url=f"attachment://{file.filename}")
    return embed, file


class PagedView(discord.ui.View):
    """Lista paginada genérica com botões ◀ ▶."""

    def __init__(self, title: str, lines: list[str], *, color=None, footer: str = ""):
        super().__init__(timeout=300)
        self.title = title
        self.pages = [lines[index:index + PAGE_SIZE] for index in range(0, len(lines), PAGE_SIZE)]
        self.pages = self.pages or [[]]
        self.color = color or discord.Color.gold()
        self.footer = footer
        self.index = 0
        self.message: discord.Message | None = None
        self._sync_buttons()

    def _sync_buttons(self) -> None:
        self.previous.disabled = self.index == 0
        self.next.disabled = self.index >= len(self.pages) - 1

    def embed(self) -> discord.Embed:
        embed = discord.Embed(
            title=self.title,
            description="\n".join(self.pages[self.index]) or "Nenhum item encontrado.",
            color=self.color,
        )
        footer = f"Página {self.index + 1}/{len(self.pages)}"
        embed.set_footer(text=f"{footer} · {self.footer}" if self.footer else footer)
        return embed

    async def _refresh(self, interaction: discord.Interaction) -> None:
        self._sync_buttons()
        await interaction.response.edit_message(embed=self.embed(), view=self)

    @discord.ui.button(label="◀", style=discord.ButtonStyle.secondary)
    async def previous(self, interaction: discord.Interaction, button) -> None:
        self.index -= 1
        await self._refresh(interaction)

    @discord.ui.button(label="▶", style=discord.ButtonStyle.secondary)
    async def next(self, interaction: discord.Interaction, button) -> None:
        self.index += 1
        await self._refresh(interaction)

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


class ScoutSelect(discord.ui.Select):
    def __init__(self, results: list[dict]):
        self.results = results
        options = []
        for index, result in enumerate(results[:25]):
            if result["type"] == "player":
                player = result["data"]
                label = (player.get("display_name") or player["name"])[:100]
                description = (
                    f"{player['overall'] or '—'} · {player['game_position'] or '—'} · "
                    f"{player['club_name'] or 'Passe livre'}"
                )
                emoji = "⚽"
            else:
                item = result["data"]
                label = item["name"][:100]
                description = item["kind"]
                emoji = "🔎"
            options.append(
                discord.SelectOption(
                    label=label, description=description[:100], value=str(index), emoji=emoji
                )
            )
        super().__init__(placeholder="Escolha um resultado para ver a ficha", options=options)

    async def callback(self, interaction: discord.Interaction) -> None:
        result = self.results[int(self.values[0])]
        if result["type"] == "player":
            embed, file = await player_embed(result["data"])
        else:
            embed, file = catalog_embed(result["data"])
        await interaction.response.edit_message(
            content=None,
            embed=embed,
            attachments=[file] if file else [],
            view=self.view,
        )


class ScoutView(discord.ui.View):
    def __init__(self, results: list[dict]):
        super().__init__(timeout=180)
        self.add_item(ScoutSelect(results))


FUTAE_SECTIONS = (
    (
        "🎲 Coleção",
        "`mm!roll` — rola um jogador (renova a cada hora cheia, horário de Brasília)\n"
        "`mm!tu` — seus timers: rolls, claim e daily\n"
        "`mm!scout <nome>` — ficha com foto de jogador, técnico, estádio, clube, uniforme…\n"
        "`mm!top [categoria]` — catálogo por overall ou nome\n"
        "`mm!left` — quanto ainda está livre em cada série\n"
        "`mm!elenco [@pessoa] [filtro]` — uma carta por página, como no Mudae",
    ),
    (
        "🪙 Economia",
        "`mm!daily` — recompensa diária\n"
        "`mm!carteira` — saldo, upgrades e limites\n"
        "`mm!upgrade [tipo]` — consulta ou compra upgrades\n"
        "`mm!crates` — abre uma crate por 500 Memilets\n"
        "`mm!wish`, `mm!wishlist`/`mm!wl`, `mm!starwish`/`mm!sw` — wishes",
    ),
    (
        "⚽ Time e partidas",
        "`mm!time` — perfil e imagem do time (`mm!time liga <nome>`)\n"
        "`mm!escalar` — monta o time clicando em cada posição\n"
        "`mm!tatica capitao|falta|penalti <nome>` — capitão e cobradores\n"
        "`mm!troca @pessoa <seu> por <dela>` / `mm!demitir <nome>`\n"
        "`mm!amistoso @pessoa` — partida narrada minuto a minuto\n"
        "`mm!campeonato` — campeonato diário com final e pênaltis",
    ),
)

GENERAL_HELP_PAGES = (
    (
        # Mesmos comandos da página 1 do mm!ajuda do memi_bot.py (sem os de admin).
        "**🏆 Rankings**\n"
        "`mm!tagarelas` — ranking de mensagens\n"
        "`mm!levels` / `mm!lvl` — níveis, patentes e XP\n"
        "`mm!hall` — vencedores dos meses anteriores\n\n"
        "**🎵 Música**\n"
        "`mm!musicas` — rankings de músicas, artistas e gêneros\n"
        "`mm!aleatoria` — sorteia uma música\n"
        "`mm!wrapped` — seu resumo dos últimos 12 meses\n\n"
        "**🎎 Mudae**\n"
        "`mm!mudae` — panorama do servidor\n"
        "`mm!mudae ios` · `personagens` · `series` — rankings do Mudae\n\n"
        "**👤 Perfil e personalização**\n"
        "`mm!perfil` · `mm!cartao` · `mm!tags` — perfil, cartão e tags\n"
        "`mm!favorita` · `mm!titulo` · `mm!frase` · `mm!ec` — edita seu perfil\n\n"
        "**ℹ️ Sobre o bot**\n"
        "`mm!changelog` — o que mudou em cada versão"
    ),
    (
        "**FUT.AE — comandos do jogo**\n"
        "Use `mm!fut.ae` para abrir diretamente esta página.\n\n"
        + "\n\n".join(
            f"{name}\n{value}" for name, value in FUTAE_SECTIONS
        )
    ),
)


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

    @commands.command(name="futae", aliases=["fut.ae", "ajudajogo", "fut"])
    async def futae_help(self, context: commands.Context) -> None:
        embed = discord.Embed(
            title="FUT.AE — comandos do jogo",
            description="Prefixo `mm!` em qualquer combinação de maiúsculas (MM!, Mm!…).",
            color=discord.Color.blurple(),
        )
        for name, value in FUTAE_SECTIONS:
            embed.add_field(name=name, value=value, inline=False)
        embed.set_footer(
            text="Use nomes, não IDs: o ID só aparece quando há homônimos. Acentos são opcionais."
        )
        await context.reply(embed=embed, mention_author=False)

    @commands.command(name="left", aliases=["livres", "restantes"])
    async def collection_left(self, context: commands.Context) -> None:
        counts = get_collection_left(self.database_path)
        lines = [
            f"{label}: **{_thousands(free)}/{_thousands(total)}**"
            for label, free, total in counts["series"]
        ]
        cartola_free, cartola_total = counts["cartola"]
        if cartola_total:
            lines.append(
                f"🟠 Cartola: **{_thousands(cartola_free)}/{_thousands(cartola_total)}**"
            )
        embed = discord.Embed(
            title="📦 Ainda livres no FUT.AE",
            description="\n".join(lines) or "Nenhum jogador elegível no pool.",
            color=discord.Color.green(),
        )
        embed.add_field(
            name="⚽ Jogadores",
            value=f"**{_thousands(counts['jogadores'])}/{_thousands(counts['jogadores_total'])}**",
            inline=True,
        )
        embed.add_field(
            name="👕 Uniformes",
            value=f"**{_thousands(counts['uniformes'])}/{_thousands(counts['uniformes_total'])}**",
            inline=True,
        )
        embed.add_field(
            name="🛡️ Logos",
            value=f"**{_thousands(counts['logos'])}/{_thousands(counts['logos_total'])}**",
            inline=True,
        )
        embed.set_footer(text="livres/total · séries pelo overall do jogo")
        await context.reply(embed=embed, mention_author=False)

    @commands.command(name="scout", aliases=["im", "info"])
    async def player_info(
        self,
        context: commands.Context,
        *,
        query: str,
    ) -> None:
        chosen, candidates = find_player(query, self.database_path, limit=15)
        players = [chosen] if chosen else candidates
        items = [] if chosen else search_catalog(query, self.database_path, limit=10)
        results = [{"type": "player", "data": player} for player in players]
        results += [{"type": "catalog", "data": item} for item in items]
        if not results:
            await context.reply(
                "Não encontrei jogador, técnico, estádio, clube ou item com esse nome.",
                mention_author=False,
            )
            return

        first = results[0]
        if first["type"] == "player":
            embed, file = await player_embed(first["data"])
        else:
            embed, file = catalog_embed(first["data"])
        view = ScoutView(results) if len(results) > 1 else None
        content = (
            f"Encontrei **{len(results)}** resultados; mostrando o mais provável. "
            "Use o menu para ver os outros."
            if view
            else None
        )
        kwargs = {"file": file} if file else {}
        await context.reply(
            content=content, embed=embed, view=view, mention_author=False, **kwargs
        )

    @commands.command(name="top")
    async def top_players(
        self,
        context: commands.Context,
        category: str = "jogadores",
    ) -> None:
        entries, total = get_catalog_entries(category, self.database_path, limit=500)
        if not entries:
            await context.reply(
                f"Categoria inválida ou sem itens. Use {TOP_CATEGORIES}.",
                mention_author=False,
            )
            return
        lines = []
        for number, entry in enumerate(entries, 1):
            if "overall" in entry:
                lines.append(
                    f"`{number:>3}.` **{entry['overall']}** — "
                    f"{entry['name']} ({entry['game_position'] or '—'})"
                )
            else:
                lines.append(f"`{number:>3}.` {entry['name']}")
        view = PagedView(f"TOP {category.upper()} ({_thousands(total)})", lines)
        view.message = await context.reply(embed=view.embed(), view=view, mention_author=False)

    @commands.command(name="elenco", aliases=["mm", "squad"])
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
                r"(?:overall\s*)?(>=|<=|>|<|=)?\s*(\d{1,3})",
                filter_text,
                re.I,
            )
            category = re.fullmatch(r"(clube|liga)\s+(.+)", filter_text, re.I)
            if position:
                filters["position"] = position.group(1).upper()
            elif overall:
                operator = overall.group(1) or "="
                rating = max(1, min(100, int(overall.group(2))))
                if operator == ">=":
                    filters["min_overall"] = rating
                elif operator == ">":
                    filters["min_overall"] = min(100, rating + 1)
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
                limit=1000,
                sort_by="overall",
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
            f"`{index:>3}.` **{player['overall'] or '—'}** — {player['name']} "
            f"({player['game_position'] or 'posição pendente'})"
            for index, player in enumerate(players, start=1)
        ]
        title = f"Elenco de {target.display_name} ({len(players)})"
        if filter_text:
            title += f" · {filter_text}"
        view = CardPagerView(players, title=title, list_lines=lines)
        await view.send(lambda **kwargs: context.reply(mention_author=False, **kwargs))

    @commands.command(name="carteira", aliases=["saldo", "wallet", "bal"])
    async def wallet(self, context: commands.Context) -> None:
        status = get_account_status(
            context.author.id, context.author.display_name, self.database_path
        )
        embed = discord.Embed(
            title=f"🪙 Carteira de {context.author.display_name}",
            description=f"# {_thousands(status.memilets)} Memilets",
            color=discord.Color.gold(),
        )
        embed.set_thumbnail(url=context.author.display_avatar.url)
        embed.add_field(name="⚽ Elenco", value=f"{status.owned_players} jogadores", inline=True)
        embed.add_field(
            name="🎲 Rolls",
            value=(
                f"{status.rolls_left}/{status.roll_capacity} no ciclo"
                + (f" + {status.roll_tokens} extras" if status.roll_tokens else "")
            ),
            inline=True,
        )
        embed.add_field(
            name="💫 Wishes",
            value=(
                f"{status.wishes}/{status.wish_capacity} · "
                f"⭐ {status.star_wishes}/{status.star_wish_capacity}"
            ),
            inline=True,
        )
        embed.add_field(
            name="⏱️ Claim", value=_timestamp(status.claim_available_at), inline=True
        )
        embed.add_field(
            name="🎁 Daily", value=_timestamp(status.daily_available_at), inline=True
        )
        embed.add_field(
            name="📦 Crates",
            value=f"Garantia {status.crate_pity}/50",
            inline=True,
        )
        embed.set_footer(text="mm!upgrade para evoluir · mm!tu para ver só os timers")
        await context.reply(embed=embed, mention_author=False)

    @commands.command(name="tu", aliases=["timers", "timer"])
    async def timers(self, context: commands.Context) -> None:
        status = get_account_status(
            context.author.id, context.author.display_name, self.database_path
        )
        extras = f" (+{status.roll_tokens} extras)" if status.roll_tokens else ""
        lines = [
            f"🎲 **{status.rolls_left}/{status.roll_capacity} rolls**{extras} · "
            f"renovam {_timestamp(status.rolls_reset_at)}",
            f"⏱️ Claim: {_timestamp(status.claim_available_at)}",
            f"🎁 Daily: {_timestamp(status.daily_available_at)}",
            f"🪙 Saldo: **{_thousands(status.memilets)} Memilets**",
        ]
        embed = discord.Embed(
            title=f"Timers de {context.author.display_name}",
            description="\n".join(lines),
            color=discord.Color.blurple(),
        )
        await context.reply(embed=embed, mention_author=False)


class StandaloneHelpCog(commands.Cog):
    """Menu geral do MeMi BOT, com o FUT.AE na segunda página."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="help", aliases=["ajuda", "comandos"])
    async def general_help(self, context: commands.Context) -> None:
        view = PagedView(
            "MeMi BOT — ajuda",
            [],
            color=discord.Color.blurple(),
            footer="Use os botões para navegar entre MeMi BOT e FUT.AE.",
        )
        view.pages = [[page] for page in GENERAL_HELP_PAGES]
        view.index = 0
        view._sync_buttons()
        view.message = await context.reply(
            embed=view.embed(),
            view=view,
            mention_author=False,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(GameCog(bot))
    if bot.get_command("help") is None:
        await bot.add_cog(StandaloneHelpCog(bot))
