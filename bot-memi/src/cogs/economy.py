"""Comandos de economia: daily, loja de upgrades, crates, wishes e demissão."""

from __future__ import annotations

import asyncio
from io import BytesIO

import discord
from discord.ext import commands
from PIL import Image

from src import icons
from src.db import DATABASE_PATH, normalize_player_name
from src.accents import rarity_color
from src.card_image import render_player_card
from src.cosmetics import COSMETIC_KINDS, list_owned
from src.crate_animation import PrizeTile, batch_opening_gif, card_reveal_gif, prize_gif
from src.economy import (
    CRATE_BATCHES,
    CRATE_DOUBLE_CHANCE,
    CRATE_PITY_LIMIT,
    CRATE_EXTRA_CHANCES,
    CRATE_MEMILETS,
    RARITY_LABELS,
    DailyCooldown,
    EconomyError,
    InsufficientFunds,
    add_wish,
    buy_upgrade,
    claim_daily,
    describe_prize,
    get_account_status,
    get_crate_pity,
    get_crate_price,
    get_upgrade_cost,
    get_upgrade_levels,
    list_wishes,
    open_crates,
    reset_claim,
)
from src.packs import PACK_PRICE, PackError, open_pack, pack_odds_text
from src.ratings import rarity_label
from src.transactions import PACK_LOCK, PACK_SALE_FACTOR
from src.cogs.common import ConfirmView, PlayerChoiceView, ambiguity_text, intro_embed
from src.game import find_player, get_wallet, register_user
from src.perks import UPGRADES

UPGRADES_INTRO = (
    "Aqui você gasta Memilets em melhorias permanentes para o seu clube, como mais rolls, "
    "wishes e um daily mais rápido. Cada upgrade tem nível máximo e pede confirmação antes "
    "de comprar. Nenhum upgrade aumenta a sorte: a chance na roleta é igual para todo mundo."
)
CRATES_INTRO = (
    "As caixas entregam itens aleatórios: rolls guardados, cosméticos, bônus e upgrades. "
    "Toda caixa traz um item básico e, com sorte, um item de outra raridade. Quanto mais rara, "
    "mais difícil de vir. A caixa vem toda em itens ou toda em Memilets, e nunca repete um "
    "cosmético que você já tem."
)

# Palavras aceitas em `mm!upgrade <nome>` além do nome de cada upgrade.
UPGRADE_ALIASES = {
    "roll": "rolls",
    "rolls": "rolls",
    "olheiro": "rolls",
    "wish": "wishes",
    "wishes": "wishes",
    "observar": "wishes",
    "starwish": "star_wishes",
    "star": "star_wishes",
    "sw": "star_wishes",
    "interesse": "star_wishes",
    "daily": "daily",
    "salario": "daily",
    "adiantar": "daily",
    "protecao": "wish_protection",
    "prioridade": "wish_protection",
    "negociacao": "wish_protection",
    "maximo": "daily_max",
    "incorporacao": "daily_max",
    "financeira": "daily_max",
    "exclusividade": "exclusivity",
    "exclusivo": "exclusivity",
    "agente": "exclusivity",
    "seguro": "dismiss_refund",
    "contrato": "dismiss_refund",
    "reembolso": "dismiss_refund",
    "demitir": "dismiss_refund",
    "bilheteria": "box_office_cap",
    "ampliar": "box_office_cap",
    "renda": "match_income",
    "patrocinio": "match_income",
}

# Mesma palavra, mesma cor em todo o bot: as cores seguem as séries das cartas.
RARITY_STYLE = {
    "basico": ("📦", discord.Color.light_grey()),
    "incomum": ("🔵", discord.Color.from_rgb(91, 156, 255)),
    "raro": ("🟣", discord.Color.from_rgb(176, 124, 255)),
    "epico": ("🟠", discord.Color.from_rgb(240, 150, 60)),
    "lendario": ("🔴", discord.Color.from_rgb(235, 75, 75)),
}
PACK_INTRO = (
    "O pacote traz **uma carta de jogador** livre do mercado, direto para o seu elenco. "
    "Nunca vem Base. A carta de pacote vende por {factor}% do valor e fica {days} dias sem "
    "poder ser vendida, doada ou trocada."
)
RARITY_ITEMS_TEXT = {
    "basico": "1 a 3 rolls guardados (não expiram)",
    "incomum": "uniforme, emblema, estádio, técnico, liga ou mascote novo",
    "raro": "-1 h no daily ou +1 s de proteção de wish",
    "epico": "+1 slot de Star Wish, +1 slot de wish ou -2 h no daily",
    "lendario": "+1 roll permanente, +1 nível de upgrade aleatório ou -10 no preço da crate",
}


def _thousands(value: int) -> str:
    return f"{value:,}".replace(",", ".")


def resolve_upgrade(text: str) -> str | None:
    wanted = normalize_player_name(text)
    if wanted.replace(" ", "_") in UPGRADES:
        return wanted.replace(" ", "_")
    for key, spec in UPGRADES.items():
        if normalize_player_name(spec.name) == wanted:
            return key
    for word in wanted.split():
        if word in UPGRADE_ALIASES:
            return UPGRADE_ALIASES[word]
    return None


def _percent(chance: float) -> str:
    return f"{chance * 100:.1f}".rstrip("0").rstrip(".").replace(".", ",") + "%"


def _range(values: tuple[int, int]) -> str:
    low, high = values
    return _thousands(low) if low == high else f"{_thousands(low)}–{_thousands(high)}"


class UpgradeSelect(discord.ui.Select):
    def __init__(self, cog: "EconomyCog", author: discord.abc.User, levels: dict[str, int]):
        self.cog = cog
        self.author = author
        options = []
        for key, spec in UPGRADES.items():
            level = levels[key]
            status = (
                "nível máximo"
                if level >= spec.limit
                else f"{_thousands(spec.cost(level))} Memilets"
            )
            options.append(
                discord.SelectOption(
                    label=f"{spec.name} · {level}/{spec.limit}"[:100],
                    description=f"{spec.effect} · {status}"[:100],
                    value=key,
                    emoji=icons.upgrade(key),
                )
            )
        super().__init__(placeholder="Escolha um upgrade para comprar", options=options)

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.author.id:
            await interaction.response.send_message(
                "Use `mm!upgrade` para abrir a sua loja.", ephemeral=True
            )
            return
        content, view = self.cog.purchase_prompt(self.author, self.values[0])
        await interaction.response.send_message(content, view=view, ephemeral=True)


class UpgradeShopView(discord.ui.View):
    def __init__(self, cog: "EconomyCog", author: discord.abc.User, levels: dict[str, int]):
        super().__init__(timeout=300)
        self.add_item(UpgradeSelect(cog, author, levels))


class CrateMenuView(discord.ui.View):
    """Abrir 1, 5 ou 10 caixas, ou um pacote de jogador, direto do menu."""

    def __init__(self, cog: "EconomyCog", author: discord.abc.User, price: int):
        super().__init__(timeout=300)
        self.cog = cog
        self.author = author
        for count in CRATE_BATCHES:
            button = discord.ui.Button(
                label=f"Abrir {count} ({_thousands(price * count)})",
                emoji="📦",
                style=discord.ButtonStyle.success if count == 1 else discord.ButtonStyle.primary,
                row=0,
            )
            button.callback = self._opener(count)
            self.add_item(button)
        pack = discord.ui.Button(
            label=f"Pacote de jogador ({_thousands(PACK_PRICE)})",
            emoji="🃏",
            style=discord.ButtonStyle.secondary,
            row=1,
        )
        pack.callback = self._pack
        self.add_item(pack)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author.id:
            await interaction.response.send_message(
                "Use `mm!crates` para abrir as suas.", ephemeral=True
            )
            return False
        return True

    def _opener(self, count: int):
        async def open_crates_button(interaction: discord.Interaction) -> None:
            await self.cog.open_with_animation(
                interaction.user,
                interaction.response.send_message,
                interaction.edit_original_response,
                count=count,
            )

        return open_crates_button

    async def _pack(self, interaction: discord.Interaction) -> None:
        await self.cog.open_pack_with_animation(
            interaction.user,
            interaction.response.send_message,
            interaction.edit_original_response,
        )


class EconomyCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.database_path = getattr(bot, "database_path", DATABASE_PATH)

    @commands.command(name="daily")
    async def daily(self, context: commands.Context) -> None:
        try:
            reward = claim_daily(
                context.author.id,
                context.author.display_name,
                self.database_path,
            )
        except DailyCooldown as error:
            unix = int(error.available_at.timestamp())
            await context.reply(
                f"Você já resgatou seu daily. O próximo libera <t:{unix}:R> (<t:{unix}:t>).",
                mention_author=False,
            )
            return
        unix = int(reward.available_at.timestamp())
        await context.reply(
            f"🎁 Você recebeu {icons.memilets(reward.amount)} Memilets "
            f"(entre 100 e {_thousands(reward.cap)}). Próximo daily <t:{unix}:R>.",
            mention_author=False,
        )

    def upgrade_menu(self, author: discord.abc.User) -> tuple[discord.Embed, UpgradeShopView]:
        levels = get_upgrade_levels(author.id, self.database_path)
        lines = []
        for key, spec in UPGRADES.items():
            level = levels[key]
            price = (
                "✅ nível máximo" if level >= spec.limit else f"{icons.memilets(spec.cost(level))}"
            )
            lines.append(
                f"{icons.upgrade(key)} **{spec.name}** · {level}/{spec.limit}\n"
                f"└ {spec.effect} · {price}"
            )
        embed = intro_embed("🛒 Loja de upgrades", UPGRADES_INTRO, color=discord.Color.gold())
        embed.description += "\n\n" + "\n".join(lines)
        balance = get_wallet(author.id, self.database_path)["memilets"]
        embed.add_field(name="Saldo", value=f"{icons.memilets(balance)} Memilets")
        embed.add_field(
            name="Somam com",
            value="Torre (`mm!construir`) e prêmios das crates (`mm!crates`)",
        )
        embed.set_footer(text="Escolha no menu ou use mm!upgrade <nome> · sempre pede confirmação")
        return embed, UpgradeShopView(self, author, levels)

    def purchase_prompt(self, author: discord.abc.User, key: str) -> tuple[str, ConfirmView | None]:
        spec = UPGRADES[key]
        level = get_upgrade_levels(author.id, self.database_path)[key]
        try:
            cost = get_upgrade_cost(key, level)
        except EconomyError as error:
            return str(error), None

        async def confirm(interaction: discord.Interaction) -> None:
            try:
                purchase = buy_upgrade(author.id, author.display_name, key, self.database_path)
            except EconomyError as error:
                await interaction.response.edit_message(content=str(error), view=None)
                return
            balance = get_wallet(author.id, self.database_path)["memilets"]
            await interaction.response.edit_message(
                content=(
                    f"✅ {icons.upgrade(key)} **{spec.name}** agora no nível "
                    f"**{purchase.level}/{spec.limit}** ({spec.effect}). "
                    f"Saldo: {icons.memilets(balance)}."
                ),
                view=None,
            )

        content = (
            f"{icons.upgrade(key)} Comprar **{spec.name}** nível {level + 1}/{spec.limit} "
            f"por {icons.memilets(cost)} Memilets?\nEfeito: {spec.effect}."
        )
        return content, ConfirmView(
            author.id, confirm, confirm_label="Comprar", cancel_text="Compra cancelada."
        )

    @commands.command(name="upgrade", aliases=["upgrades", "loja"])
    async def upgrade(
        self,
        context: commands.Context,
        *,
        upgrade_name: str | None = None,
    ) -> None:
        register_user(context.author.id, context.author.display_name, self.database_path)
        if not upgrade_name:
            embed, view = self.upgrade_menu(context.author)
            await context.reply(embed=embed, view=view, mention_author=False)
            return
        key = resolve_upgrade(upgrade_name)
        if key is None:
            await context.reply(
                "Upgrade inválido. Veja as opções em `mm!upgrade`.",
                mention_author=False,
            )
            return
        content, view = self.purchase_prompt(context.author, key)
        message = await context.reply(content, view=view, mention_author=False)
        if view:
            view.message = message

    def crate_menu(self, author: discord.abc.User) -> tuple[discord.Embed, CrateMenuView]:
        price = get_crate_price(author.id, self.database_path)
        pity = get_crate_pity(author.id, self.database_path)
        embed = intro_embed("📦 Crates", CRATES_INTRO, color=discord.Color.orange())
        lines = [
            f"{RARITY_STYLE['basico'][0]} **Básico** · sempre vem\n"
            f"└ {RARITY_ITEMS_TEXT['basico']} **ou** {_range(CRATE_MEMILETS['basico'])} Memilets"
        ]
        for rarity in ("incomum", "raro", "epico", "lendario"):
            lines.append(
                f"{RARITY_STYLE[rarity][0]} **{RARITY_LABELS[rarity]}** · "
                f"{_percent(CRATE_EXTRA_CHANCES[rarity])}\n"
                f"└ {RARITY_ITEMS_TEXT[rarity]} **ou** {_range(CRATE_MEMILETS[rarity])} Memilets"
            )
        lines.append(
            f"🎁 {_percent(CRATE_DOUBLE_CHANCE)} de chance de o item vir em dobro "
            "(não vale para Memilets)."
        )
        embed.description += "\n\n" + "\n".join(lines)
        balance = get_wallet(author.id, self.database_path)["memilets"]
        embed.add_field(name="Preço", value=f"{icons.memilets(price)} por caixa")
        embed.add_field(name="Saldo", value=f"{icons.memilets(balance)} Memilets")
        embed.add_field(
            name="🛡️ Garantia de Raro",
            value=f"**{pity}/{CRATE_PITY_LIMIT}** · faltam {CRATE_PITY_LIMIT - pity} caixas",
        )
        odds = " · ".join(
            f"{emoji} {label} {_percent(chance)}" for emoji, label, chance in pack_odds_text()
        )
        embed.add_field(
            name=f"🃏 Pacote de jogador · {icons.memilets(PACK_PRICE)}",
            value=f"Uma carta livre do mercado: {odds}",
            inline=False,
        )
        embed.set_footer(
            text="mm!crates 5 abre 5 direto · mm!pacote abre um pacote · "
            "rolls da caixa ficam guardados"
        )
        return embed, CrateMenuView(self, author, price)

    def prize_tiles(self, user_id: int, prizes) -> list[PrizeTile]:
        """Cartões do GIF: Memilets e rolls somados; cada item no seu cartão."""
        order = list(RARITY_LABELS)
        tiles: list[PrizeTile] = []
        coins = [prize for prize in prizes if prize.kind == "memilets"]
        rolls = [prize for prize in prizes if prize.kind == "instant_rolls"]
        if coins:
            best = max((prize.rarity for prize in coins), key=order.index)
            total = sum(prize.amount for prize in coins)
            tiles.append(PrizeTile(best, "coin", _thousands(total), "Memilets"))
        if rolls:
            total = sum(prize.amount for prize in rolls)
            tiles.append(
                PrizeTile("basico", "rolls", f"{total} roll{'s' if total > 1 else ''}", "guardados")
            )
        pictures = {}
        for prize in prizes:
            if prize.kind.startswith("cosmetic:") and not pictures:
                pictures = {
                    (item["kind"], item["name"]): item["image_path"]
                    for item in list_owned(user_id, None, self.database_path)
                }
        items = {
            "daily_1h": ("clock", "-1 h", "no daily"),
            "daily_2h": ("clock", "-2 h", "no daily"),
            "wish_protection": ("shield", "+1 s", "proteção de wish"),
            "star_wish_slot": ("star", "+1 Star Wish", "slot permanente"),
            "wish_slot": ("plus", "+1 wish", "slot permanente"),
            "permanent_roll": ("rolls", "+1 roll", "por ciclo, para sempre"),
            "crate_discount": ("tag", "-10", "no preço da crate"),
        }
        for prize in sorted(prizes, key=lambda item: -order.index(item.rarity)):
            if prize.kind in {"memilets", "instant_rolls"}:
                continue
            if prize.kind.startswith("cosmetic:"):
                kind = prize.kind.split(":", 1)[1]
                tiles.append(
                    PrizeTile(
                        prize.rarity,
                        "cosmetic",
                        COSMETIC_KINDS[kind][0],
                        prize.name or "",
                        pictures.get((kind, prize.name)),
                    )
                )
            elif prize.kind == "random_upgrade":
                tiles.append(PrizeTile(prize.rarity, "up", "+1 nível", prize.name or ""))
            else:
                icon, title, subtitle = items[prize.kind]
                tiles.append(PrizeTile(prize.rarity, icon, title, subtitle))
        # Itens raros primeiro, para aparecerem nos cartões do GIF.
        return sorted(tiles, key=lambda tile: -order.index(tile.rarity))

    async def open_with_animation(self, author, send, edit, *, count: int = 1) -> None:
        """O pacote treme e depois vem a revelação com os prêmios já à vista.

        `send(**kwargs)` manda a primeira mensagem; `edit(**kwargs)` a troca pela revelação.
        """
        try:
            results = open_crates(author.id, author.display_name, count, self.database_path)
        except InsufficientFunds as error:
            await send(embed=discord.Embed(description=str(error), color=discord.Color.red()))
            return
        # Resposta rápida em texto; a revelação (com o pacote abrindo) entra no lugar dela.
        waiting = discord.Embed(
            title=f"📦 Abrindo {count} crate{'s' if count > 1 else ''}…",
            color=discord.Color.from_rgb(230, 181, 74),
        )
        message = await send(embed=waiting)
        prizes = [prize for result in results for prize in result.prizes]
        best = max((result.best_rarity for result in results), key=list(RARITY_LABELS).index)
        if count > 1:
            # Lote: o GIF só mostra o pacote abrindo e a melhor raridade; os prêmios de
            # cada caixa vêm em lista no texto.
            counts: dict[str, int] = {}
            for result in results:
                counts[result.best_rarity] = counts.get(result.best_rarity, 0) + 1
            reveal = await asyncio.to_thread(batch_opening_gif, counts)
            embed = self.crate_batch_embed(author, results)
        else:
            reveal = await asyncio.to_thread(prize_gif, best, self.prize_tiles(author.id, prizes))
            embed = self.crate_result_embed(author, results)
        embed.set_image(url="attachment://revelacao.gif")
        target = edit if edit is not None else message.edit
        await target(embed=embed, attachments=[discord.File(BytesIO(reveal), "revelacao.gif")])

    def crate_batch_embed(self, author: discord.abc.User, results) -> discord.Embed:
        """Lote de caixas: totais e destaques no topo, depois caixa por caixa."""
        order = list(RARITY_LABELS)
        best = max((result.best_rarity for result in results), key=order.index)
        emoji, color = RARITY_STYLE[best]
        prizes = [prize for result in results for prize in result.prizes]
        coins = sum(prize.amount for prize in prizes if prize.kind == "memilets")
        rolls = sum(prize.amount for prize in prizes if prize.kind == "instant_rolls")
        summary = []
        if coins:
            summary.append(f"{icons.memilets(coins)} Memilets no total")
        if rolls:
            summary.append(f"🎲 **{rolls}** rolls guardados (use quando quiser)")
        specials = [
            prize
            for prize in sorted(prizes, key=lambda item: -order.index(item.rarity))
            if prize.kind not in {"memilets", "instant_rolls"}
        ]
        if specials:
            summary.append("")
            summary.append("**Itens:**")
            summary.extend(
                f"{RARITY_STYLE[prize.rarity][0]} **{RARITY_LABELS[prize.rarity]}** · "
                + describe_prize(prize)
                for prize in specials
            )
        if any(result.guaranteed for result in results):
            summary.append(f"🛡️ **Garantia ativada:** {CRATE_PITY_LIMIT} caixas sem Raro, veio um.")
        embed = discord.Embed(
            title=f"{emoji} {len(results)} crates · melhor: {RARITY_LABELS[best]}",
            description="\n".join(summary),
            color=color,
        )
        for number, result in enumerate(results, 1):
            lines = []
            for prize in result.prizes:
                if prize.kind == "memilets":
                    lines.append(f"{RARITY_STYLE[prize.rarity][0]} {icons.memilets(prize.amount)}")
                elif prize.kind == "instant_rolls":
                    lines.append(f"🎲 {prize.amount} roll{'s' if prize.amount > 1 else ''}")
                else:
                    lines.append(f"{RARITY_STYLE[prize.rarity][0]} {describe_prize(prize)}")
            if result.doubled:
                lines.append("🎁 em dobro")
            embed.add_field(
                name=f"Caixa {number} · {RARITY_LABELS[result.best_rarity]}",
                value="\n".join(lines)[:1024] or "—",
                inline=True,
            )
        balance = get_wallet(author.id, self.database_path)["memilets"]
        spent = sum(result.price for result in results)
        embed.set_footer(
            text=f"Pagou {spent:,} · saldo {balance:,} Memilets · garantia de Raro "
            f"{results[-1].pity}/{CRATE_PITY_LIMIT}".replace(",", ".")
        )
        return embed

    def crate_result_embed(self, author: discord.abc.User, results) -> discord.Embed:
        if not isinstance(results, list):
            results = [results]
        order = list(RARITY_LABELS)
        best = max((result.best_rarity for result in results), key=order.index)
        emoji, color = RARITY_STYLE[best]
        prizes = [prize for result in results for prize in result.prizes]
        lines = []
        coins = sum(prize.amount for prize in prizes if prize.kind == "memilets")
        rolls = sum(prize.amount for prize in prizes if prize.kind == "instant_rolls")
        if coins:
            lines.append(f"{icons.memilets(coins)} Memilets")
        if rolls:
            lines.append(
                f"🎲 **{rolls}** roll{'s' if rolls > 1 else ''} guardado{'s' if rolls > 1 else ''} (use quando quiser)"
            )
        for prize in sorted(prizes, key=lambda item: -order.index(item.rarity)):
            if prize.kind in {"memilets", "instant_rolls"}:
                continue
            lines.append(
                f"{RARITY_STYLE[prize.rarity][0]} **{RARITY_LABELS[prize.rarity]}** · "
                + describe_prize(prize)
            )
        doubled = sum(1 for result in results if result.doubled)
        if doubled:
            lines.append(
                "🎁 **Sorte grande!** Prêmio em dobro" + (f" ({doubled}×)." if doubled > 1 else ".")
            )
        if any(result.guaranteed for result in results):
            lines.append(f"🛡️ **Garantia ativada:** {CRATE_PITY_LIMIT} caixas sem Raro, veio um.")
        count = len(results)
        embed = discord.Embed(
            title=f"{emoji} {count} crate{'s' if count > 1 else ''} · melhor: {RARITY_LABELS[best]}",
            description="\n".join(lines),
            color=color,
        )
        balance = get_wallet(author.id, self.database_path)["memilets"]
        spent = sum(result.price for result in results)
        embed.add_field(name="Pagou", value=f"{icons.memilets(spent)}")
        embed.add_field(name="Saldo", value=f"{icons.memilets(balance)} Memilets")
        embed.add_field(
            name="🛡️ Garantia de Raro", value=f"{results[-1].pity}/{CRATE_PITY_LIMIT} caixas"
        )
        if any(prize.kind.startswith("cosmetic:") for prize in prizes):
            embed.set_footer(
                text="Veja e equipe em mm!cosmeticos · cada cosmético equipado: +100 no daily"
            )
        return embed

    async def open_pack_with_animation(self, author, send, edit) -> None:
        try:
            result = await asyncio.to_thread(
                open_pack, author.id, author.display_name, self.database_path
            )
        except PackError as error:
            await send(embed=discord.Embed(description=str(error), color=discord.Color.red()))
            return
        player = result.player
        overall = int(player["effective_overall"] or 0)
        waiting = discord.Embed(
            title="🃏 Abrindo o pacote…", color=discord.Color.from_rgb(230, 181, 74)
        )
        message = await send(embed=waiting)
        label = rarity_label(overall)
        card = await asyncio.to_thread(render_player_card, {**player, "effective_overall": overall})
        reveal = await asyncio.to_thread(
            card_reveal_gif,
            Image.open(card),
            rarity_color(overall).to_rgb(),
            f"PACOTE · {label.split(' ', 1)[-1].upper()}",
        )
        name = player.get("display_name") or player["name"]
        embed = discord.Embed(
            title=f"🃏 {name}",
            description=(
                f"**{overall}** · {label} · {player.get('game_position') or '—'} · "
                f"{player.get('club_name') or 'Passe livre'}\n"
                f"Foi direto para o seu elenco.\n"
                f"📦 Vende por {round(PACK_SALE_FACTOR * 100)}% do valor · livre para vender, "
                f"doar ou trocar <t:{int(result.locked_until.timestamp())}:R>."
            ),
            color=rarity_color(overall),
        )
        balance = get_wallet(author.id, self.database_path)["memilets"]
        embed.add_field(name="Pagou", value=f"{icons.memilets(result.price)}")
        embed.add_field(name="Saldo", value=f"{icons.memilets(balance)} Memilets")
        embed.set_image(url="attachment://pacote.gif")
        target = edit if edit is not None else message.edit
        await target(embed=embed, attachments=[discord.File(BytesIO(reveal), "pacote.gif")])

    @commands.command(name="crates", aliases=["crate", "caixa", "caixas"])
    async def crates(self, context: commands.Context, action: str | None = None) -> None:
        register_user(context.author.id, context.author.display_name, self.database_path)
        wanted = normalize_player_name(action or "")
        count = 1 if wanted in {"abrir", "open"} else int(wanted) if wanted.isdigit() else None
        if count is not None:
            if count not in CRATE_BATCHES:
                await context.reply(
                    "Dá para abrir 1, 5 ou 10 de uma vez: `mm!crates 5`.", mention_author=False
                )
                return
            await self.open_with_animation(
                context.author,
                lambda **kwargs: context.reply(mention_author=False, **kwargs),
                None,
                count=count,
            )
            return
        embed, view = self.crate_menu(context.author)
        await context.reply(embed=embed, view=view, mention_author=False)

    @commands.command(name="pacote", aliases=["pack", "pacotes"])
    async def pack(self, context: commands.Context, action: str | None = None) -> None:
        register_user(context.author.id, context.author.display_name, self.database_path)
        if normalize_player_name(action or "") in {"abrir", "open", "1"}:
            await self.open_pack_with_animation(
                context.author,
                lambda **kwargs: context.reply(mention_author=False, **kwargs),
                None,
            )
            return
        odds = "\n".join(
            f"{emoji} **{label}** · {_percent(chance)}" for emoji, label, chance in pack_odds_text()
        )
        embed = intro_embed(
            "🃏 Pacote de jogador",
            PACK_INTRO.format(factor=round(PACK_SALE_FACTOR * 100), days=PACK_LOCK.days),
            color=discord.Color.from_rgb(230, 181, 74),
        )
        embed.description += "\n\n" + odds
        balance = get_wallet(context.author.id, self.database_path)["memilets"]
        embed.add_field(name="Preço", value=f"{icons.memilets(PACK_PRICE)}")
        embed.add_field(name="Saldo", value=f"{icons.memilets(balance)} Memilets")

        async def done(interaction: discord.Interaction) -> None:
            await self.open_pack_with_animation(
                interaction.user,
                interaction.response.send_message,
                interaction.edit_original_response,
            )

        view = ConfirmView(
            context.author.id,
            done,
            confirm_label=f"Abrir pacote ({_thousands(PACK_PRICE)})",
            cancel_text="Pacote cancelado.",
        )
        view.message = await context.reply(embed=embed, view=view, mention_author=False)

    @commands.command(name="rt")
    async def reset_claim_command(self, context: commands.Context) -> None:
        try:
            available_at = reset_claim(
                context.author.id, context.author.display_name, self.database_path
            )
        except DailyCooldown as error:
            unix = int(error.available_at.timestamp())
            await context.reply(
                f"Você já usou o `mm!rt`. Ele volta <t:{unix}:R> (<t:{unix}:t>).",
                mention_author=False,
            )
            return
        except EconomyError as error:
            await context.reply(str(error), mention_author=False)
            return
        unix = int(available_at.timestamp())
        await context.reply(
            f"{icons.upgrade('claim_reset')} Claim liberado! Pode contratar agora. "
            f"O `mm!rt` volta <t:{unix}:R>.",
            mention_author=False,
        )

    @commands.command(name="wish")
    async def wish(
        self,
        context: commands.Context,
        *,
        query: str | None = None,
    ) -> None:
        if not query:
            await self._show_wishes(context, "normal")
            return
        await self._toggle_wish(context, query, "normal")

    def _wish_header(self, user_id: int, display_name: str) -> str:
        status = get_account_status(user_id, display_name, self.database_path)
        return (
            f"💫 Wishes: **{status.wishes}/{status.wish_capacity}** · "
            f"⭐ Star Wishes: **{status.star_wishes}/{status.star_wish_capacity}**"
        )

    @commands.command(name="wishlist", aliases=["wl"])
    async def wishlist(self, context: commands.Context) -> None:
        header = self._wish_header(context.author.id, context.author.display_name)
        wishes = list_wishes(context.author.id, self.database_path)
        lines = [
            f"{'⭐' if wish['wish_type'] == 'star' else '•'} {wish['name']} — "
            f"{wish['club_name'] or 'Passe livre'}"
            for wish in wishes
        ]
        embed = discord.Embed(
            title=f"Wishlist de {context.author.display_name}",
            description=header
            + "\n\n"
            + ("\n".join(lines) or "Nenhuma wish ainda. Use `mm!wish <nome>`."),
            color=discord.Color.gold(),
        )
        embed.set_footer(text="⭐ = Star Wish · mm!wish <nome> adiciona ou remove")
        await context.reply(embed=embed, mention_author=False)

    @commands.command(name="starwish", aliases=["sw"])
    async def star_wish(
        self,
        context: commands.Context,
        *,
        query: str | None = None,
    ) -> None:
        if not query:
            await self._show_wishes(context, "star")
            return
        await self._toggle_wish(context, query, "star")

    async def _toggle_wish(
        self,
        context: commands.Context,
        query: str,
        wish_type: str,
    ) -> None:
        player, candidates = find_player(query, self.database_path)
        if player is not None:
            message = self._apply_wish(context.author.id, player, wish_type)
            await context.reply(message, mention_author=False)
            return
        if not candidates:
            await context.reply(ambiguity_text(query, []), mention_author=False)
            return

        async def chosen(interaction: discord.Interaction, picked: dict) -> None:
            await interaction.response.edit_message(
                content=self._apply_wish(context.author.id, picked, wish_type), view=None
            )

        await context.reply(
            ambiguity_text(query, candidates),
            view=PlayerChoiceView(context.author.id, candidates, chosen),
            mention_author=False,
        )

    def _apply_wish(self, user_id: int, player: dict, wish_type: str) -> str:
        name = player.get("display_name") or player["name"]
        kind_label = "Star Wish" if wish_type == "star" else "wish"
        try:
            result = add_wish(user_id, player["id"], wish_type, self.database_path)
        except EconomyError as error:
            return str(error)
        if result == "removed":
            return f"Removi **{name}** da sua {kind_label}."
        if result == "updated":
            return f"Atualizei **{name}** para {kind_label}."
        return f"{'⭐' if wish_type == 'star' else '💫'} Adicionei **{name}** à sua {kind_label}."

    async def _show_wishes(
        self,
        context: commands.Context,
        wish_type: str,
    ) -> None:
        header = self._wish_header(context.author.id, context.author.display_name)
        wishes = [
            wish
            for wish in list_wishes(context.author.id, self.database_path)
            if wish["wish_type"] == wish_type
        ]
        label = "Star Wishes" if wish_type == "star" else "Wishes"
        lines = [
            f"{'⭐' if wish_type == 'star' else '•'} {wish['name']} — "
            f"{wish['club_name'] or 'Passe livre'}"
            for wish in wishes
        ]
        embed = discord.Embed(
            title=f"{label} de {context.author.display_name}",
            description=header + "\n\n" + ("\n".join(lines) or "Nenhuma ainda."),
            color=discord.Color.gold() if wish_type == "star" else discord.Color.blurple(),
        )
        await context.reply(embed=embed, mention_author=False)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(EconomyCog(bot))
