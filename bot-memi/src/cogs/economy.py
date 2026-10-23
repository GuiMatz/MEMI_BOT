"""Comandos de economia: daily, loja, upgrades, crates, pacotes, wishes e demissão."""

from __future__ import annotations

import asyncio
from functools import partial
from io import BytesIO

import discord
from discord.ext import commands
from PIL import Image

from src import icons
from src.db import DATABASE_PATH, normalize_player_name
from src.accents import rarity_color
from src.card_image import render_player_card
from src.cosmetics import COSMETIC_KINDS, list_owned
from src.crate_animation import PrizeTile, all_in_wheel_gif, card_reveal_gif, prize_gif
from src.lineup_image import TIER_COLORS
from src.economy import (
    CRATE_BATCHES,
    CRATE_DOUBLE_CHANCE,
    CRATE_PITY_LIMIT,
    EPIC_PITY_LIMIT,
    CRATE_EXTRA_CHANCES,
    CRATE_MEMILETS,
    EPIC_CRATE_EXTRA_CHANCES,
    EPIC_CRATE_MULTIPLIER,
    RARITY_LABELS,
    ROLLS_PER_BONUS_CLAIM,
    DailyCooldown,
    EconomyError,
    InsufficientFunds,
    activate_rolls,
    add_wish,
    buy_upgrade,
    claim_daily,
    describe_prize,
    get_account_status,
    get_crate_pity,
    get_epic_pity,
    get_crate_price,
    get_upgrade_cost,
    get_upgrade_levels,
    list_wishes,
    open_crates,
    reset_claim,
)
from src.packs import (
    ALL_IN_PRICE,
    ALL_IN_TOP,
    ALL_IN_WHEEL,
    EPIC_PACK_PRICE,
    PACK_PRICE,
    PackError,
    open_all_in,
    open_pack,
    pack_odds_text,
)
from src.ratings import RARITY_TIERS, rarity_label
from src.transactions import PACK_LOCK, PACK_SALE_FACTOR
from src.cogs.common import ConfirmView, PlayerChoiceView, ambiguity_text, intro_embed
from src.game import find_player, get_wallet, register_user
from src.missions import board, claim_rewards
from src.inventory import INVENTORY_ITEMS, get_inventory
from src.perks import DAILY_MIN_AMOUNT, TOWER_FIRST_PRICE, UPGRADES

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
SHOP_INTRO = (
    "Tudo o que dá para comprar com Memilets. A **caixa épica** nunca vem só com o básico; "
    "o **pacote épico** só traz Raro ou melhor. Os botões abaixo compram direto."
)
EPIC_PACK_INTRO = (
    "O pacote épico traz **uma carta Raro ou melhor**, com chance de Ícone. "
    "Diferente do pacote comum, a carta vende pelo **valor cheio**. Ela fica {lock} "
    "sem poder ser doada ou trocada."
)
ALL_IN_INTRO = (
    "**Uma aposta, não um investimento.** A roleta tem {top} de chance de parar numa "
    "fatia dourada e dar um **Ícone ou melhor**. Nas outras, sai uma carta como a do "
    "pacote comum. Se não houver nenhum Ícone livre no mercado, a roleta não gira e nada "
    "é cobrado. A carta vem marcada como de pacote (vende na hora por 50%)."
)
# Tempo da roleta no GIF (giro + a fatia piscando) antes de a mensagem virar a carta.
ALL_IN_SPIN_SECONDS = 6
PACK_INTRO = (
    "O pacote traz **uma carta de jogador** livre do mercado, direto para o seu elenco. "
    "Nunca vem Base. A carta de pacote pode ser vendida na hora, por {factor}% do valor, e "
    "fica {lock} sem poder ser doada ou trocada."
)
RARITY_ITEMS_TEXT = {
    "basico": "1 a 3 rolls guardados (não expiram)",
    "incomum": "uniforme, emblema, estádio, técnico, liga ou mascote novo",
    "raro": "-1 h no daily, +1 s de proteção de wish, +1 caixa ou pacote de jogador",
    "epico": "+1 slot de Star Wish, +1 slot de wish, -2 h no daily, caixa épica ou pacote "
    "de jogador",
    "lendario": "+1 roll permanente, +1 nível de upgrade aleatório, -10 no preço da crate ou "
    "pacote épico",
}


EPIC_WORDS = {"epica", "epico", "epic"}
# "Abrir todas" do inventário abre no máximo isso por vez (o texto lista caixa por caixa).
INVENTORY_BATCH_LIMIT = 20


def _crate_noun(count: int, epic: bool) -> str:
    if epic:
        return "caixas épicas" if count > 1 else "caixa épica"
    return "crates" if count > 1 else "crate"


def _duration(span) -> str:
    """ "1 hora", "2 horas", "7 dias": o tempo da trava em texto."""
    hours = int(span.total_seconds() // 3600)
    if hours % 24 == 0:
        days = hours // 24
        return f"{days} dia{'s' if days > 1 else ''}"
    return f"{hours} hora{'s' if hours > 1 else ''}"


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
        self.balance = get_wallet(author.id, cog.database_path)["memilets"]
        pity = get_crate_pity(author.id, cog.database_path)
        for count in CRATE_BATCHES:
            # A caixa avulsa mostra quanto falta para a garantia de Raro.
            extra = f" · Raro {pity}/{CRATE_PITY_LIMIT}" if count == 1 else ""
            self._buy_button(
                f"Abrir {count}",
                price * count,
                "📦",
                discord.ButtonStyle.success if count == 1 else discord.ButtonStyle.primary,
                0,
                self._opener(count),
                extra,
            )
        self._buy_button(
            "Caixa épica",
            price * EPIC_CRATE_MULTIPLIER,
            "🟠",
            discord.ButtonStyle.secondary,
            1,
            self._opener(1, epic=True),
        )
        self._buy_button(
            "Pacote de jogador",
            PACK_PRICE,
            "🃏",
            discord.ButtonStyle.secondary,
            1,
            self._pack(False),
        )
        self._buy_button(
            "Pacote épico",
            EPIC_PACK_PRICE,
            "🃏",
            discord.ButtonStyle.secondary,
            1,
            self._pack(True),
        )

    def _buy_button(self, name, cost, emoji, style, row, callback, extra="") -> discord.ui.Button:
        """Botão de compra; sem saldo, fica cinza e diz quanto falta."""
        missing = cost - self.balance
        label = f"{name} ({_thousands(cost)})" + (
            f" · faltam {_thousands(missing)}" if missing > 0 else extra
        )
        button = discord.ui.Button(
            label=label[:80], emoji=emoji, style=style, row=row, disabled=missing > 0
        )
        button.callback = callback
        self.add_item(button)
        return button

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author.id:
            await interaction.response.send_message(
                "Use `mm!crates` para abrir as suas.", ephemeral=True
            )
            return False
        return True

    def _opener(self, count: int, *, epic: bool = False):
        async def open_crates_button(interaction: discord.Interaction) -> None:
            await self.cog.open_with_animation(
                interaction.user,
                interaction.response.send_message,
                interaction.edit_original_response,
                count=count,
                epic=epic,
            )

        return open_crates_button

    def _pack(self, epic: bool):
        async def open_pack_button(interaction: discord.Interaction) -> None:
            await self.cog.open_pack_with_animation(
                interaction.user,
                interaction.response.send_message,
                interaction.edit_original_response,
                epic=epic,
            )

        return open_pack_button


class DailyMissionsView(discord.ui.View):
    """No daily: resgata as missões prontas sem precisar abrir o `mm!missoes`."""

    def __init__(self, cog: "EconomyCog", owner, ready: int):
        super().__init__(timeout=300)
        self.cog = cog
        self.owner = owner
        button = discord.ui.Button(
            label=f"Resgatar missões ({ready})", emoji="🎁", style=discord.ButtonStyle.success
        )
        button.callback = self._collect
        self.add_item(button)

    async def _collect(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.owner.id:
            await interaction.response.send_message(
                "Use `mm!daily` ou `mm!missoes` para as suas.", ephemeral=True
            )
            return
        total, count = claim_rewards(self.owner.id, self.cog.database_path)
        notice = (
            f"🎁 {count} missão(ões) resgatada(s): +{icons.memilets(total)} Memilets."
            if count
            else "Nenhuma missão pronta para resgatar agora."
        )
        content = (interaction.message.content if interaction.message else "") + "\n\n" + notice
        await interaction.response.edit_message(content=content[:2000], view=None)


class InventoryView(discord.ui.View):
    """Um botão "Abrir" por item do inventário; abre com a animação de sempre, sem cobrar."""

    def __init__(self, cog: "EconomyCog", author: discord.abc.User, items: dict[str, int]):
        super().__init__(timeout=300)
        self.cog = cog
        self.author = author
        for item, amount in items.items():
            emoji, name = INVENTORY_ITEMS[item]
            button = discord.ui.Button(
                label=f"Abrir {name.lower()} ({amount})",
                emoji=emoji,
                style=discord.ButtonStyle.success,
            )
            button.callback = self._opener(item)
            self.add_item(button)
        crates = items.get("caixa", 0) + items.get("caixa_epica", 0)
        if crates > 1:
            batch = min(crates, INVENTORY_BATCH_LIMIT)
            everything = discord.ui.Button(
                label=f"Abrir todas as caixas ({batch})",
                emoji="📦",
                style=discord.ButtonStyle.primary,
                row=1,
            )
            everything.callback = self._open_all
            self.add_item(everything)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author.id:
            await interaction.response.send_message(
                "Use `mm!inventario` para ver o seu.", ephemeral=True
            )
            return False
        return True

    async def _open_all(self, interaction: discord.Interaction) -> None:
        await self.cog.open_all_inventory_crates(
            interaction.user, interaction.response.send_message, interaction.edit_original_response
        )

    def _opener(self, item: str):
        async def open_item(interaction: discord.Interaction) -> None:
            await self.cog.open_inventory_item(
                interaction.user,
                item,
                interaction.response.send_message,
                interaction.edit_original_response,
            )

        return open_item


class ShopView(CrateMenuView):
    """Loja: os mesmos botões de compra das caixas, mais o atalho para os upgrades."""

    def __init__(self, cog: "EconomyCog", author: discord.abc.User, price: int):
        super().__init__(cog, author, price)
        upgrades = discord.ui.Button(
            label="Upgrades", emoji="🛒", style=discord.ButtonStyle.secondary, row=2
        )
        upgrades.callback = self._upgrades
        self.add_item(upgrades)
        self._buy_button("All-in", ALL_IN_PRICE, "🎰", discord.ButtonStyle.danger, 2, self._all_in)
        inventory = discord.ui.Button(
            label="Inventário", emoji="🎒", style=discord.ButtonStyle.secondary, row=2
        )
        inventory.callback = self._inventory
        self.add_item(inventory)

    async def _inventory(self, interaction: discord.Interaction) -> None:
        embed, view = self.cog.inventory_menu(interaction.user)
        await interaction.response.send_message(embed=embed, view=view)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author.id:
            await interaction.response.send_message(
                "Use `mm!shop` para abrir a sua loja.", ephemeral=True
            )
            return False
        return True

    async def _upgrades(self, interaction: discord.Interaction) -> None:
        embed, view = self.cog.upgrade_menu(interaction.user)
        await interaction.response.send_message(embed=embed, view=view)

    async def _all_in(self, interaction: discord.Interaction) -> None:
        # Aposta alta: sempre passa pelo aviso e pela confirmação.
        embed, view = self.cog.all_in_prompt(interaction.user)
        await interaction.response.send_message(embed=embed, view=view)
        view.message = await interaction.original_response()


class EconomyCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.database_path = getattr(bot, "database_path", DATABASE_PATH)

    @commands.command(name="daily")
    async def daily(self, context: commands.Context) -> None:
        content, view = self.daily_reply(context.author)
        await context.reply(content, view=view, mention_author=False)

    def daily_reply(self, user) -> tuple[str, discord.ui.View | None]:
        """Resgata o daily e mostra as missões do dia, com o botão de resgatar as prontas."""
        try:
            reward = claim_daily(user.id, user.display_name, self.database_path)
        except DailyCooldown as error:
            unix = int(error.available_at.timestamp())
            text = f"Você já resgatou seu daily. O próximo libera <t:{unix}:R> (<t:{unix}:t>)."
        else:
            unix = int(reward.available_at.timestamp())
            text = (
                f"🎁 Você recebeu {icons.memilets(reward.amount)} Memilets "
                f"(entre {_thousands(DAILY_MIN_AMOUNT)} e {_thousands(reward.cap)}). "
                f"Próximo daily <t:{unix}:R>."
            )
        rows = board(user.id, self.database_path)
        ready = sum(1 for row in rows if row["progress"] >= row["goal"] and not row["claimed"])
        lines = [text, "", "**🎯 Missões**"]
        for row in rows:
            done = row["progress"] >= row["goal"]
            mark = "✅" if row["claimed"] else ("🎁" if done else "⬜")
            lines.append(
                f"{mark} {row['text']} · {min(row['progress'], row['goal'])}/{row['goal']} · "
                f"+{_thousands(row['reward'])}"
            )
        view = DailyMissionsView(self, user, ready) if ready else None
        return "\n".join(lines), view

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

    def shop_menu(self, author: discord.abc.User) -> tuple[discord.Embed, ShopView]:
        price = get_crate_price(author.id, self.database_path)
        epic_price = price * EPIC_CRATE_MULTIPLIER
        embed = intro_embed("🏪 Loja", SHOP_INTRO, color=discord.Color.gold())

        def odds(epic: bool) -> str:
            return " · ".join(
                f"{emoji} {_percent(chance)}" for emoji, _label, chance in pack_odds_text(epic)
            )

        def crate_odds(chances: dict[str, float]) -> str:
            return " · ".join(
                f"{RARITY_STYLE[rarity][0]} {_percent(chances[rarity])}"
                for rarity in ("incomum", "raro", "epico", "lendario")
            )

        embed.add_field(
            name="📦 Caixas",
            value=(
                f"**Caixa** · {icons.memilets(price)}\n└ {crate_odds(CRATE_EXTRA_CHANCES)}\n"
                f"**Caixa épica** · {icons.memilets(epic_price)}\n"
                f"└ {crate_odds(EPIC_CRATE_EXTRA_CHANCES)}\n"
                "Detalhes em `mm!crates`"
            ),
            inline=False,
        )
        embed.add_field(
            name="🃏 Pacotes de jogador",
            value=(
                f"**Pacote** · {icons.memilets(PACK_PRICE)}\n└ {odds(False)}\n"
                f"**Pacote épico** · {icons.memilets(EPIC_PACK_PRICE)}\n└ {odds(True)}\n"
                "Detalhes em `mm!pacote` e `mm!pacote epico`"
            ),
            inline=False,
        )
        levels = get_upgrade_levels(author.id, self.database_path)
        open_upgrades = sum(1 for key, spec in UPGRADES.items() if levels[key] < spec.limit)
        embed.add_field(
            name=f"🎰 All-in · {icons.memilets(ALL_IN_PRICE)}",
            value="Uma aposta: roleta com 1 chance em 10 de Ícone ou melhor · `mm!allin`",
            inline=False,
        )
        embed.add_field(
            name="🛒 Upgrades",
            value=(
                f"{len(UPGRADES)} melhorias permanentes ({open_upgrades} ainda com nível "
                "para comprar) · `mm!upgrade`"
            ),
            inline=False,
        )
        embed.add_field(
            name="🏗️ Torre",
            value=(
                f"12 andares, a partir de {icons.memilets(TOWER_FIRST_PRICE)} · " "`mm!construir`"
            ),
            inline=False,
        )
        balance = get_wallet(author.id, self.database_path)["memilets"]
        embed.add_field(name="Saldo", value=f"{icons.memilets(balance)} Memilets")
        embed.set_footer(text="Toda compra tira o valor do saldo na hora")
        return embed, ShopView(self, author, price)

    @commands.command(name="shop", aliases=["loja", "store"])
    async def shop(self, context: commands.Context) -> None:
        register_user(context.author.id, context.author.display_name, self.database_path)
        embed, view = self.shop_menu(context.author)
        await context.reply(embed=embed, view=view, mention_author=False)

    @commands.command(name="upgrade", aliases=["upgrades"])
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
        epic_pity = get_epic_pity(author.id, self.database_path)
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
            name="🛡️ Garantias",
            value=(
                f"Raro **{pity}/{CRATE_PITY_LIMIT}** · faltam {CRATE_PITY_LIMIT - pity}\n"
                f"Épico **{epic_pity}/{EPIC_PITY_LIMIT}** · faltam {EPIC_PITY_LIMIT - epic_pity}"
            ),
        )
        epic_odds = " · ".join(
            f"{RARITY_STYLE[rarity][0]} {_percent(EPIC_CRATE_EXTRA_CHANCES[rarity])}"
            for rarity in ("incomum", "raro", "epico", "lendario")
        )
        embed.add_field(
            name=f"🟠 Caixa épica · {icons.memilets(price * EPIC_CRATE_MULTIPLIER)}",
            value=f"Nunca vem só o básico: o item extra sempre vem · {epic_odds}",
            inline=False,
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
            text="mm!crates 5 abre 5 direto · mm!crates epica abre uma épica · "
            "mm!shop mostra a loja inteira"
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
            "bonus_crate": ("box", "+1 caixa", "no inventário"),
            "epic_crate": ("box", "+1 caixa", "épica, no inventário"),
            "player_pack": ("card", "+1 pacote", "no inventário"),
            "epic_pack": ("card", "+1 pacote", "épico, no inventário"),
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

    async def open_with_animation(
        self,
        author,
        send,
        edit,
        *,
        count: int = 1,
        epic: bool = False,
        from_inventory: bool = False,
    ) -> None:
        """O pacote treme e depois vem a revelação com os prêmios já à vista.

        `send(**kwargs)` manda a primeira mensagem; `edit(**kwargs)` a troca pela revelação.
        """
        try:
            results = open_crates(
                author.id,
                author.display_name,
                count,
                self.database_path,
                epic=epic,
                from_inventory=from_inventory,
            )
        except InsufficientFunds as error:
            await send(embed=discord.Embed(description=str(error), color=discord.Color.red()))
            return
        await self.reveal_crates(author, results, send, edit, epic=epic)

    async def open_all_inventory_crates(self, author, send, edit) -> None:
        """ "Abrir todas" do inventário: caixas e caixas épicas num lote só (até 20)."""
        items = get_inventory(author.id, self.database_path)
        normal = min(items.get("caixa", 0), INVENTORY_BATCH_LIMIT)
        epic = min(items.get("caixa_epica", 0), INVENTORY_BATCH_LIMIT - normal)
        results = []
        try:
            for amount, is_epic in ((normal, False), (epic, True)):
                if amount:
                    results += open_crates(
                        author.id,
                        author.display_name,
                        amount,
                        self.database_path,
                        epic=is_epic,
                        from_inventory=True,
                    )
        except InsufficientFunds as error:
            if not results:
                await send(embed=discord.Embed(description=str(error), color=discord.Color.red()))
                return
        if not results:
            await send(
                embed=discord.Embed(
                    description="Não há caixas no inventário.", color=discord.Color.red()
                )
            )
            return
        await self.reveal_crates(author, results, send, edit, epic=normal == 0)

    async def reveal_crates(self, author, results, send, edit, *, epic: bool = False) -> None:
        """Manda "Abrindo…" e troca pela revelação (GIF + texto) das caixas já abertas."""
        count = len(results)
        # Resposta rápida em texto; a revelação (com o pacote abrindo) entra no lugar dela.
        waiting = discord.Embed(
            title=f"📦 Abrindo {count} {_crate_noun(count, epic)}…",
            color=discord.Color.from_rgb(230, 181, 74),
        )
        message = await send(embed=waiting)
        prizes = [prize for result in results for prize in result.prizes]
        best = max((result.best_rarity for result in results), key=list(RARITY_LABELS).index)
        if count > 1:
            # Lote: o GIF mostra quantas caixas de cada raridade e os prêmios somados;
            # o que saiu em cada caixa vem em lista no texto.
            counts: dict[str, int] = {}
            for result in results:
                counts[result.best_rarity] = counts.get(result.best_rarity, 0) + 1
            reveal = await asyncio.to_thread(
                prize_gif, best, self.prize_tiles(author.id, prizes), counts=counts
            )
            embed = self.crate_batch_embed(author, results, epic=epic)
        else:
            reveal = await asyncio.to_thread(prize_gif, best, self.prize_tiles(author.id, prizes))
            embed = self.crate_result_embed(author, results, epic=epic)
        embed.set_image(url="attachment://revelacao.gif")
        target = edit if edit is not None else message.edit
        await target(embed=embed, attachments=[discord.File(BytesIO(reveal), "revelacao.gif")])

    def crate_batch_embed(
        self, author: discord.abc.User, results, *, epic: bool = False
    ) -> discord.Embed:
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
            summary.append(f"🎲 **{rolls}** rolls guardados (ative com `mm!ativar <quantidade>`)")
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
        if any(result.guaranteed_epic for result in results):
            summary.append(
                f"🛡️ **Garantia de Épico:** {EPIC_PITY_LIMIT} caixas sem Épico, veio um."
            )
        embed = discord.Embed(
            title=f"{emoji} {len(results)} {_crate_noun(len(results), epic)} · "
            f"melhor: {RARITY_LABELS[best]}",
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
            f"{results[-1].pity}/{CRATE_PITY_LIMIT} · de Épico "
            f"{results[-1].epic_pity}/{EPIC_PITY_LIMIT}".replace(",", ".")
        )
        return embed

    def crate_result_embed(
        self, author: discord.abc.User, results, *, epic: bool = False
    ) -> discord.Embed:
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
                f"🎲 **{rolls}** roll{'s' if rolls > 1 else ''} guardado{'s' if rolls > 1 else ''} "
                "(ative com `mm!ativar <quantidade>`)"
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
        if any(result.guaranteed_epic for result in results):
            lines.append(f"🛡️ **Garantia de Épico:** {EPIC_PITY_LIMIT} caixas sem Épico, veio um.")
        count = len(results)
        embed = discord.Embed(
            title=f"{emoji} {count} {_crate_noun(count, epic)} · melhor: {RARITY_LABELS[best]}",
            description="\n".join(lines),
            color=color,
        )
        balance = get_wallet(author.id, self.database_path)["memilets"]
        spent = sum(result.price for result in results)
        embed.add_field(name="Pagou", value=f"{icons.memilets(spent)}")
        embed.add_field(name="Saldo", value=f"{icons.memilets(balance)} Memilets")
        embed.add_field(
            name="🛡️ Garantias",
            value=f"Raro {results[-1].pity}/{CRATE_PITY_LIMIT} · "
            f"Épico {results[-1].epic_pity}/{EPIC_PITY_LIMIT}",
        )
        if any(prize.kind.startswith("cosmetic:") for prize in prizes):
            embed.set_footer(
                text="Veja e equipe em mm!cosmeticos · cada cosmético equipado: +250 no daily"
            )
        return embed

    async def open_pack_with_animation(
        self, author, send, edit, *, epic: bool = False, from_inventory: bool = False
    ) -> None:
        try:
            result = await asyncio.to_thread(
                partial(open_pack, epic=epic, from_inventory=from_inventory),
                author.id,
                author.display_name,
                self.database_path,
            )
        except PackError as error:
            await send(embed=discord.Embed(description=str(error), color=discord.Color.red()))
            return
        waiting = discord.Embed(
            title=f"🃏 Abrindo o pacote{' épico' if epic else ''}…",
            color=discord.Color.from_rgb(230, 181, 74),
        )
        message = await send(embed=waiting)
        embed, reveal = await self.card_reveal(
            author, result, "PACOTE ÉPICO" if epic else "PACOTE", full_value=epic
        )
        target = edit if edit is not None else message.edit
        await target(embed=embed, attachments=[discord.File(BytesIO(reveal), "pacote.gif")])

    async def card_reveal(
        self, author, result, headline: str, *, full_value: bool = False
    ) -> tuple[discord.Embed, bytes]:
        """Embed e GIF da carta que saiu num pacote ou no all-in (`full_value`: pacote épico,
        cuja carta vende pelo valor cheio)."""
        player = result.player
        overall = int(player["effective_overall"] or 0)
        label = rarity_label(overall)
        card = await asyncio.to_thread(render_player_card, {**player, "effective_overall": overall})
        reveal = await asyncio.to_thread(
            card_reveal_gif,
            Image.open(card),
            rarity_color(overall).to_rgb(),
            f"{headline} · {label.split(' ', 1)[-1].upper()}",
        )
        name = player.get("display_name") or player["name"]
        embed = discord.Embed(
            title=f"🃏 {name}",
            description=(
                f"**{overall}** · {label} · {player.get('game_position') or '—'} · "
                f"{player.get('club_name') or 'Passe livre'}\n"
                f"Foi direto para o seu elenco.\n"
                + (
                    "📦 Vende pelo valor cheio · "
                    if full_value
                    else f"📦 Dá para vender na hora por {round(PACK_SALE_FACTOR * 100)}% do valor · "
                )
                + f"livre para doar ou trocar <t:{int(result.locked_until.timestamp())}:R>."
            ),
            color=rarity_color(overall),
        )
        balance = get_wallet(author.id, self.database_path)["memilets"]
        embed.add_field(name="Pagou", value=f"{icons.memilets(result.price)}")
        embed.add_field(name="Saldo", value=f"{icons.memilets(balance)} Memilets")
        embed.set_image(url="attachment://pacote.gif")
        return embed, reveal

    def all_in_prompt(self, author: discord.abc.User) -> tuple[discord.Embed, ConfirmView]:
        """Aviso do all-in (é uma aposta) com o botão de girar."""
        top = sum(1 for tier in ALL_IN_WHEEL if tier in ALL_IN_TOP) / len(ALL_IN_WHEEL)
        lines = [ALL_IN_INTRO.format(top=_percent(top)), ""]
        for tier in sorted(set(ALL_IN_WHEEL)):
            if tier in ALL_IN_TOP:
                continue
            chance = ALL_IN_WHEEL.count(tier) / len(ALL_IN_WHEEL)
            lines.append(
                f"{RARITY_TIERS[tier][1]} **{RARITY_TIERS[tier][0]}** · {_percent(chance)}"
            )
        lines.append(
            f"⭐ **Ícone ou melhor** · {_percent(top)} ("
            + ", ".join(
                f"{RARITY_TIERS[tier][0]} {_percent(top * w)}" for tier, w in ALL_IN_TOP.items()
            )
            + ")"
        )
        embed = intro_embed(
            "🎰 All-in", "\n".join(lines), color=discord.Color.from_rgb(230, 181, 74)
        )
        balance = get_wallet(author.id, self.database_path)["memilets"]
        embed.add_field(name="Preço", value=f"{icons.memilets(ALL_IN_PRICE)}")
        embed.add_field(name="Saldo", value=f"{icons.memilets(balance)} Memilets")

        async def spin(interaction: discord.Interaction) -> None:
            await self.all_in_with_animation(
                interaction.user,
                interaction.response.send_message,
                interaction.edit_original_response,
            )

        view = ConfirmView(
            author.id,
            spin,
            confirm_label=f"Girar ({_thousands(ALL_IN_PRICE)})",
            cancel_text="All-in cancelado.",
        )
        return embed, view

    async def all_in_with_animation(self, author, send, edit) -> None:
        """A roleta gira e, quando para, a mensagem vira a revelação da carta."""
        try:
            result = await asyncio.to_thread(
                open_all_in, author.id, author.display_name, self.database_path
            )
        except PackError as error:
            await send(embed=discord.Embed(description=str(error), color=discord.Color.red()))
            return
        labels = {index: tier[0].upper() for index, tier in enumerate(RARITY_TIERS)}
        wheel = await asyncio.to_thread(
            all_in_wheel_gif,
            ALL_IN_WHEEL,
            result.slot,
            TIER_COLORS,
            labels,
            top_label="ÍCONE OU MELHOR",
            price=f"{ALL_IN_PRICE // 1000}K",
        )
        spinning = discord.Embed(
            title="🎰 All-in · girando…", color=discord.Color.from_rgb(230, 181, 74)
        )
        spinning.set_image(url="attachment://roleta.gif")
        message = await send(embed=spinning, file=discord.File(BytesIO(wheel), "roleta.gif"))
        embed, reveal = await self.card_reveal(author, result, "ALL-IN")
        await asyncio.sleep(ALL_IN_SPIN_SECONDS)
        target = edit if edit is not None else message.edit
        await target(embed=embed, attachments=[discord.File(BytesIO(reveal), "pacote.gif")])

    @commands.command(name="allin", aliases=["all-in", "aposta"])
    async def all_in(self, context: commands.Context) -> None:
        register_user(context.author.id, context.author.display_name, self.database_path)
        embed, view = self.all_in_prompt(context.author)
        view.message = await context.reply(embed=embed, view=view, mention_author=False)

    @commands.command(name="crates", aliases=["crate", "caixa", "caixas"])
    async def crates(
        self, context: commands.Context, action: str | None = None, amount: str | None = None
    ) -> None:
        register_user(context.author.id, context.author.display_name, self.database_path)
        wanted = normalize_player_name(action or "")
        epic = wanted in EPIC_WORDS
        if epic:
            wanted = normalize_player_name(amount or "1")
        count = 1 if wanted in {"abrir", "open"} else int(wanted) if wanted.isdigit() else None
        if count is not None:
            if count not in CRATE_BATCHES:
                await context.reply(
                    "Dá para abrir 1, 5 ou 10 de uma vez: `mm!crates 5` ou `mm!crates epica 5`.",
                    mention_author=False,
                )
                return
            await self.open_with_animation(
                context.author,
                lambda **kwargs: context.reply(mention_author=False, **kwargs),
                None,
                count=count,
                epic=epic,
            )
            return
        embed, view = self.crate_menu(context.author)
        await context.reply(embed=embed, view=view, mention_author=False)

    @commands.command(name="pacote", aliases=["pack", "pacotes"])
    async def pack(
        self, context: commands.Context, action: str | None = None, amount: str | None = None
    ) -> None:
        register_user(context.author.id, context.author.display_name, self.database_path)
        wanted = normalize_player_name(action or "")
        epic = wanted in EPIC_WORDS
        if epic:
            wanted = normalize_player_name(amount or "")
        if wanted in {"abrir", "open", "1"}:
            await self.open_pack_with_animation(
                context.author,
                lambda **kwargs: context.reply(mention_author=False, **kwargs),
                None,
                epic=epic,
            )
            return
        price = EPIC_PACK_PRICE if epic else PACK_PRICE
        odds = "\n".join(
            f"{emoji} **{label}** · {_percent(chance)}"
            for emoji, label, chance in pack_odds_text(epic)
        )
        embed = intro_embed(
            "🃏 Pacote épico" if epic else "🃏 Pacote de jogador",
            (EPIC_PACK_INTRO if epic else PACK_INTRO).format(
                factor=round(PACK_SALE_FACTOR * 100), lock=_duration(PACK_LOCK)
            ),
            color=discord.Color.from_rgb(230, 181, 74),
        )
        embed.description += "\n\n" + odds
        balance = get_wallet(context.author.id, self.database_path)["memilets"]
        embed.add_field(name="Preço", value=f"{icons.memilets(price)}")
        embed.add_field(name="Saldo", value=f"{icons.memilets(balance)} Memilets")
        if not epic:
            embed.set_footer(text="Pacote épico (só Raro ou melhor): mm!pacote epico")

        async def done(interaction: discord.Interaction) -> None:
            await self.open_pack_with_animation(
                interaction.user,
                interaction.response.send_message,
                interaction.edit_original_response,
                epic=epic,
            )

        view = ConfirmView(
            context.author.id,
            done,
            confirm_label=f"Abrir pacote{' épico' if epic else ''} ({_thousands(price)})",
            cancel_text="Pacote cancelado.",
        )
        view.message = await context.reply(embed=embed, view=view, mention_author=False)

    async def open_inventory_item(self, author, item: str, send, edit) -> None:
        """Abre uma caixa ou pacote ganho, com a mesma animação da compra."""
        if item in {"caixa", "caixa_epica"}:
            await self.open_with_animation(
                author, send, edit, epic=item == "caixa_epica", from_inventory=True
            )
        else:
            await self.open_pack_with_animation(
                author, send, edit, epic=item == "pacote_epico", from_inventory=True
            )

    def inventory_menu(self, author: discord.abc.User) -> tuple[discord.Embed, InventoryView]:
        items = get_inventory(author.id, self.database_path)
        embed = discord.Embed(title="🎒 Inventário", color=discord.Color.gold())
        if items:
            embed.description = "\n".join(
                f"{INVENTORY_ITEMS[item][0]} **{INVENTORY_ITEMS[item][1]}** × {amount}"
                for item, amount in items.items()
            )
            embed.set_footer(text="Abrir não custa nada: você já ganhou")
        else:
            embed.description = (
                "Vazio por enquanto. As caixas podem dar **caixas**, **caixas épicas** e "
                "**pacotes de jogador**, que ficam guardados aqui (`mm!crates`)."
            )
        return embed, InventoryView(self, author, items)

    @commands.command(name="inventario", aliases=["inventário", "inv", "mochila"])
    async def inventory(self, context: commands.Context) -> None:
        register_user(context.author.id, context.author.display_name, self.database_path)
        embed, view = self.inventory_menu(context.author)
        await context.reply(embed=embed, view=view, mention_author=False)

    @commands.command(name="ativar", aliases=["ativarrolls", "usarrolls"])
    async def activate(self, context: commands.Context, amount: str | None = None) -> None:
        """Ativa os rolls guardados das caixas: +1 contratação a cada 10."""
        wanted = (amount or "").strip()
        if not wanted.isdigit():
            # Sempre com a quantidade: a pessoa escolhe quantos guardados quer gastar.
            stored = get_account_status(
                context.author.id, context.author.display_name, self.database_path
            ).roll_tokens
            await context.reply(
                f"Diga quantos rolls quer ativar: `mm!ativar 20`. Você tem **{stored}** "
                f"guardados; cada {ROLLS_PER_BONUS_CLAIM} ativados dão +1 contratação.",
                mention_author=False,
            )
            return
        try:
            result = activate_rolls(
                context.author.id, context.author.display_name, self.database_path, int(wanted)
            )
        except EconomyError as error:
            await context.reply(str(error), mention_author=False)
            return
        lines = [
            f"🎲 **{result.activated}** rolls ativados: valem depois dos rolls do ciclo "
            f"(agora você tem **{result.active_rolls}** ativos)."
        ]
        if result.claims_gained:
            lines.append(
                f"✅ **+{result.claims_gained}** contratação(ões) extra(s) · total: "
                f"**{result.bonus_claims}**. Elas valem quando o claim do ciclo já foi usado."
            )
        missing = ROLLS_PER_BONUS_CLAIM - result.carry
        lines.append(
            f"Faltam **{missing}** rolls ativados para a próxima contratação extra "
            f"(1 a cada {ROLLS_PER_BONUS_CLAIM})."
        )
        await context.reply("\n".join(lines), mention_author=False)

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
