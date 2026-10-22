"""Comandos de economia: daily, loja de upgrades, crates, wishes e demissão."""

from __future__ import annotations

import asyncio
from io import BytesIO

import discord
from discord.ext import commands

from src import icons
from src.db import DATABASE_PATH, normalize_player_name
from src.crate_animation import opening_gif, reveal_gif
from src.economy import (
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
    get_crate_price,
    get_upgrade_cost,
    get_upgrade_levels,
    list_wishes,
    open_crate,
    reset_claim,
)
from src.cogs.common import ConfirmView, PlayerChoiceView, ambiguity_text, intro_embed
from src.game import find_player, get_wallet, list_owned_players, register_user, resolve_player
from src.perks import UPGRADES
from src.transactions import TransactionError, dismiss_player

UPGRADES_INTRO = (
    "Aqui você gasta Memilets em melhorias permanentes para o seu clube, como mais rolls, "
    "wishes e um daily mais rápido. Cada upgrade tem nível máximo e pede confirmação antes "
    "de comprar. Nenhum upgrade aumenta a sorte: a chance na roleta é igual para todo mundo."
)
CRATES_INTRO = (
    "As caixas entregam itens aleatórios: rolls instantâneos, cosméticos, bônus e upgrades. "
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

RARITY_STYLE = {
    "basico": ("📦", discord.Color.light_grey()),
    "incomum": ("🟢", discord.Color.green()),
    "raro": ("🔵", discord.Color.blue()),
    "epico": ("🟣", discord.Color.purple()),
    "lendario": ("🟠", discord.Color.orange()),
}
RARITY_ITEMS_TEXT = {
    "basico": "1 a 3 rolls instantâneos (só no ciclo atual)",
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
    def __init__(self, cog: "EconomyCog", author: discord.abc.User, price: int):
        super().__init__(timeout=300)
        self.cog = cog
        self.author = author
        self.open_button.label = f"Abrir crate ({_thousands(price)})"

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author.id:
            await interaction.response.send_message(
                "Use `mm!crates` para abrir as suas.", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="Abrir crate", emoji="📦", style=discord.ButtonStyle.success)
    async def open_button(self, interaction: discord.Interaction, button) -> None:
        price = get_crate_price(interaction.user.id, self.cog.database_path)
        button.label = f"Abrir outra ({_thousands(price)})"
        await self.cog.open_with_animation(
            interaction.user,
            interaction.response.send_message,
            interaction.edit_original_response,
        )


class DismissConfirmationView(discord.ui.View):
    def __init__(self, user_id: int, player_id: int, database_path, player_name: str):
        super().__init__(timeout=120)
        self.user_id = user_id
        self.player_id = player_id
        self.database_path = database_path
        self.player_name = player_name

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "Somente quem iniciou a demissão pode confirmar.", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="Confirmar demissão", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button) -> None:
        try:
            refund = dismiss_player(self.user_id, self.player_id, self.database_path)
        except TransactionError as error:
            await interaction.response.edit_message(content=str(error), view=None)
            self.stop()
            return
        await interaction.response.edit_message(
            content=(
                f"Jogador **{self.player_name}** liberado. "
                f"Você recebeu {icons.memilets(refund)} Memilets."
            ),
            view=None,
        )
        self.stop()

    @discord.ui.button(label="Cancelar", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button) -> None:
        await interaction.response.edit_message(content="Demissão cancelada.", view=None)
        self.stop()


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
        lines.append(
            f"🛡️ Garantia: a cada {CRATE_PITY_LIMIT} caixas sem Raro, a seguinte traz um Raro."
        )
        embed.description += "\n\n" + "\n".join(lines)
        balance = get_wallet(author.id, self.database_path)["memilets"]
        embed.add_field(name="Preço", value=f"{icons.memilets(price)} Memilets")
        embed.add_field(name="Saldo", value=f"{icons.memilets(balance)} Memilets")
        embed.set_footer(text="mm!crates abrir abre direto · mm!cosmeticos mostra o que você tem")
        return embed, CrateMenuView(self, author, price)

    async def open_with_animation(self, author, send, edit) -> None:
        """Abre a crate em dois tempos: o pacote treme e depois revela a raridade.

        `send(**kwargs)` manda a primeira mensagem; `edit(**kwargs)` a troca pela revelação.
        """
        try:
            result = open_crate(author.id, author.display_name, self.database_path)
        except InsufficientFunds as error:
            await send(embed=discord.Embed(description=str(error), color=discord.Color.red()))
            return
        opening = await asyncio.to_thread(opening_gif)
        waiting = discord.Embed(
            title="📦 Abrindo a crate…", color=discord.Color.from_rgb(230, 181, 74)
        )
        waiting.set_image(url="attachment://abrindo.gif")
        message = await send(embed=waiting, file=discord.File(BytesIO(opening), "abrindo.gif"))
        reveal = await asyncio.to_thread(reveal_gif, result.best_rarity)
        await asyncio.sleep(1.6)
        embed = self.crate_result_embed(author, result)
        embed.set_image(url="attachment://revelacao.gif")
        target = edit if edit is not None else message.edit
        await target(embed=embed, attachments=[discord.File(BytesIO(reveal), "revelacao.gif")])

    def crate_result_embed(self, author: discord.abc.User, result) -> discord.Embed:
        emoji, color = RARITY_STYLE[result.best_rarity]
        lines = [
            f"{RARITY_STYLE[prize.rarity][0]} **{RARITY_LABELS[prize.rarity]}** · "
            + (
                f"{icons.memilets(prize.amount)} Memilets"
                if prize.kind == "memilets"
                else describe_prize(prize)
            )
            for prize in result.prizes
        ]
        if result.doubled:
            lines.append("🎁 **Sorte grande!** O prêmio veio em dobro.")
        embed = discord.Embed(
            title=f"{emoji} Crate aberta · {RARITY_LABELS[result.best_rarity]}",
            description="\n".join(lines),
            color=color,
        )
        balance = get_wallet(author.id, self.database_path)["memilets"]
        embed.add_field(name="Pagou", value=f"{icons.memilets(result.price)}")
        embed.add_field(name="Saldo", value=f"{icons.memilets(balance)} Memilets")
        if result.guaranteed:
            embed.description += "\n🛡️ **Garantia ativada:** 40 caixas sem Raro, esta veio com um."
        embed.add_field(name="Garantia de Raro", value=f"{result.pity}/{CRATE_PITY_LIMIT} caixas")
        if any(prize.kind.startswith("cosmetic:") for prize in result.prizes):
            embed.set_footer(
                text="Veja e equipe em mm!cosmeticos · cada cosmético equipado: +100 no daily"
            )
        return embed

    @commands.command(name="crates", aliases=["crate", "caixa", "caixas"])
    async def crates(self, context: commands.Context, action: str | None = None) -> None:
        register_user(context.author.id, context.author.display_name, self.database_path)
        if action and normalize_player_name(action) in {"abrir", "open", "1"}:
            await self.open_with_animation(
                context.author,
                lambda **kwargs: context.reply(mention_author=False, **kwargs),
                None,
            )
            return
        embed, view = self.crate_menu(context.author)
        await context.reply(embed=embed, view=view, mention_author=False)

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

    @commands.command(name="demitir")
    async def dismiss(self, context: commands.Context, *, query: str) -> None:
        owned = list_owned_players(context.author.id, self.database_path, limit=5000)
        player, candidates = resolve_player(query, owned)

        async def confirm(send, chosen: dict) -> None:
            view = DismissConfirmationView(
                context.author.id, int(chosen["id"]), self.database_path, chosen["name"]
            )
            await send(content=f"Confirma a demissão de **{chosen['name']}**?", view=view)

        if player is not None:
            await confirm(lambda **kwargs: context.reply(mention_author=False, **kwargs), player)
            return
        if not candidates:
            await context.reply(f"Não achei **{query[:60]}** no seu elenco.", mention_author=False)
            return

        async def chosen(interaction: discord.Interaction, picked: dict) -> None:
            await confirm(interaction.response.edit_message, picked)

        await context.reply(
            ambiguity_text(query, candidates),
            view=PlayerChoiceView(context.author.id, candidates, chosen),
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
