"""Comandos de economia, upgrades e wishes."""

from __future__ import annotations

import discord
from discord.ext import commands

from src.db import DATABASE_PATH, normalize_player_name
from src.economy import (
    CRATE_PITY_LIMIT,
    CRATE_PRICE,
    DAILY_TIMEZONE,
    DailyCooldown,
    EconomyError,
    InsufficientFunds,
    UPGRADE_LABELS,
    UPGRADE_LIMITS,
    add_wish,
    buy_upgrade,
    claim_daily,
    get_upgrade_cost,
    get_upgrade_levels,
    list_wishes,
    open_crate,
)
from src.game import get_wallet, register_user, search_players
from src.game import list_owned_players
from src.transactions import TransactionError, dismiss_player

UPGRADE_ALIASES = {
    "roll": "rolls",
    "rolls": "rolls",
    "wish": "wishes",
    "wishes": "wishes",
    "daily": "daily",
    "exclusividade": "exclusivity",
    "exclusiva": "exclusivity",
    "exclusivity": "exclusivity",
    "duplicata": "duplicate_bonus",
    "duplicatas": "duplicate_bonus",
    "duplicate": "duplicate_bonus",
    "duplicate_bonus": "duplicate_bonus",
}

UPGRADE_COMMANDS = {
    "rolls": "roll",
    "wishes": "wish",
    "daily": "daily",
    "exclusivity": "exclusividade",
    "duplicate_bonus": "duplicata",
}

REWARD_MESSAGES = {
    "memilets": lambda amount: f"🪙 **{amount} Memilets**",
    "jackpot": lambda amount: f"💰 **JACKPOT! {amount} Memilets**",
    "roll_tokens": lambda amount: f"🎲 **{amount} rolls extras**",
    "claim_reset": lambda amount: "⏱️ **Claim liberado agora** (cooldown zerado)",
    "star_wishes": lambda amount: "⭐ **+1 slot permanente de Star Wish**",
    "wishes": lambda amount: "💫 **+1 slot permanente de wish**",
    "rolls": lambda amount: "🔁 **+1 roll permanente por ciclo**",
}

REWARD_RARITY = {
    "memilets": ("Comum", discord.Color.light_grey()),
    "roll_tokens": ("Comum", discord.Color.green()),
    "claim_reset": ("Raro", discord.Color.blue()),
    "jackpot": ("Épico", discord.Color.purple()),
    "wishes": ("Lendário", discord.Color.gold()),
    "rolls": ("Lendário", discord.Color.gold()),
    "star_wishes": ("Lendário", discord.Color.gold()),
}


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
                f"Você recebeu **{refund} Memilets**."
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
            local_time = error.available_at.astimezone(DAILY_TIMEZONE)
            await context.reply(
                "Você já resgatou seu daily. O próximo fica disponível em "
                f"**{local_time:%d/%m às %H:%M}** (horário de Brasília).",
                mention_author=False,
            )
            return
        await context.reply(
            f"🎁 Você recebeu **{reward.amount} Memilets** "
            f"(máx: **{reward.cap}**).",
            mention_author=False,
        )

    @commands.command(name="upgrade")
    async def upgrade(
        self,
        context: commands.Context,
        upgrade_name: str | None = None,
    ) -> None:
        register_user(context.author.id, context.author.display_name, self.database_path)
        if not upgrade_name:
            levels = get_upgrade_levels(context.author.id, self.database_path)
            lines = []
            for key, label in UPGRADE_LABELS.items():
                level = levels[key]
                if level >= UPGRADE_LIMITS[key]:
                    cost_text = "✅ nível máximo"
                elif key == "star_wishes":
                    cost_text = "📦 só em crates"
                else:
                    cost = f"{get_upgrade_cost(key, level):,}".replace(",", ".")
                    cost_text = f"🪙 {cost} · `mm!upgrade {UPGRADE_COMMANDS[key]}`"
                lines.append(
                    f"**{label[:1].upper()}{label[1:]}** — nível "
                    f"{level}/{UPGRADE_LIMITS[key]}\n└ {cost_text}"
                )
            embed = discord.Embed(
                title="Upgrades",
                description="\n".join(lines),
                color=discord.Color.gold(),
            )
            balance = get_wallet(context.author.id, self.database_path)["memilets"]
            embed.set_footer(
                text=f"Saldo: {balance:,} Memilets · Star Wish extra só sai em crate.".replace(",", ".")
            )
            await context.reply(embed=embed, mention_author=False)
            return

        key = UPGRADE_ALIASES.get(normalize_player_name(upgrade_name))
        if key is None:
            await context.reply(
                "Upgrade inválido. Consulte `mm!upgrade` para ver as opções.",
                mention_author=False,
            )
            return
        try:
            purchase = buy_upgrade(
                context.author.id,
                context.author.display_name,
                key,
                self.database_path,
            )
        except InsufficientFunds as error:
            await context.reply(str(error), mention_author=False)
            return
        except EconomyError as error:
            await context.reply(str(error), mention_author=False)
            return
        balance = get_wallet(context.author.id, self.database_path)["memilets"]
        await context.reply(
            f"✅ Upgrade **{UPGRADE_LABELS[key]}** comprado: nível "
            f"**{purchase.level}/{UPGRADE_LIMITS[key]}** por "
            f"**{purchase.cost:,} Memilets**. Saldo: **{balance:,}**.".replace(",", "."),
            mention_author=False,
        )

    @commands.command(name="crates")
    async def crates(self, context: commands.Context, amount: int = 1) -> None:
        if amount != 1:
            await context.reply(
                "Por segurança, abra uma crate por vez: `mm!crates`.",
                mention_author=False,
            )
            return
        try:
            reward = open_crate(
                context.author.id,
                context.author.display_name,
                self.database_path,
            )
        except InsufficientFunds as error:
            await context.reply(str(error), mention_author=False)
            return
        rarity, color = REWARD_RARITY[reward.kind]
        balance = get_wallet(context.author.id, self.database_path)["memilets"]
        embed = discord.Embed(
            title=f"📦 Crate aberta · {rarity}",
            description=REWARD_MESSAGES[reward.kind](reward.amount),
            color=color,
        )
        embed.add_field(name="Saldo", value=f"{balance:,} Memilets".replace(",", "."))
        embed.add_field(
            name="Garantia",
            value=f"{reward.pity}/{CRATE_PITY_LIMIT} até um prêmio permanente",
        )
        embed.set_footer(
            text=f"Cada crate custa {CRATE_PRICE} Memilets · mm!crates para abrir outra"
        )
        await context.reply(embed=embed, mention_author=False)

    @commands.command(name="demitir")
    async def dismiss(self, context: commands.Context, *, query: str) -> None:
        try:
            player_id = int(query.strip())
        except ValueError:
            owned = list_owned_players(
                context.author.id,
                self.database_path,
                limit=1000,
            )
            matches = [
                player for player in owned
                if normalize_player_name(query) in normalize_player_name(player["name"])
            ]
            if len(matches) != 1:
                await context.reply(
                    "Informe o ID de um jogador do seu elenco ou refine o nome.",
                    mention_author=False,
                )
                return
            player_id = int(matches[0]["id"])
        player = next(
            (item for item in list_owned_players(
                context.author.id, self.database_path, limit=1000
            ) if int(item["id"]) == player_id),
            None,
        )
        if player is None:
            await context.reply("Esse jogador não pertence ao seu elenco.", mention_author=False)
            return
        view = DismissConfirmationView(
            context.author.id, player_id, self.database_path, player["name"]
        )
        await context.reply(
            f"Confirma a demissão de **{player['name']}**?",
            view=view,
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

    @commands.command(name="wishlist", aliases=["wl"])
    async def wishlist(self, context: commands.Context) -> None:
        wishes = list_wishes(context.author.id, self.database_path)
        if not wishes:
            await context.reply("Você ainda não tem wishes.", mention_author=False)
            return
        lines = [
            f"{'⭐ ' if wish['wish_type'] == 'star' else ''}• "
            f"`{wish['player_id']}` {wish['name']} — "
            f"{wish['club_name'] or 'clube desconhecido'}"
            for wish in wishes
        ]
        embed = discord.Embed(
            title=f"Wishlist de {context.author.display_name}",
            description="\n".join(lines),
            color=discord.Color.gold(),
        )
        embed.set_footer(text="⭐ = Star Wish")
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
        players = search_players(query, self.database_path, limit=5)
        if not players:
            await context.reply("Não encontrei esse jogador.", mention_author=False)
            return
        if len(players) > 1:
            lines = "\n".join(
                f"`{player['id']}` {player['display_name'] or player['name']} — "
                f"{player['club_name'] or 'clube desconhecido'}"
                for player in players
            )
            await context.reply(
                "Encontrei vários jogadores. Tente usar o ID:\n" + lines,
                mention_author=False,
            )
            return
        player = players[0]
        result = add_wish(
            context.author.id,
            player["id"],
            wish_type,
            self.database_path,
        )
        kind_label = "Star Wish" if wish_type == "star" else "wish"
        if result == "removed":
            message = f"Removi **{player['display_name'] or player['name']}** da sua {kind_label}."
        elif result == "updated":
            message = f"Atualizei **{player['display_name'] or player['name']}** para {kind_label}."
        else:
            message = f"Adicionei **{player['display_name'] or player['name']}** à sua {kind_label}."
        await context.reply(message, mention_author=False)

    async def _show_wishes(
        self,
        context: commands.Context,
        wish_type: str,
    ) -> None:
        wishes = [
            wish for wish in list_wishes(context.author.id, self.database_path)
            if wish["wish_type"] == wish_type
        ]
        if not wishes:
            await context.reply("Você ainda não tem wishes desse tipo.", mention_author=False)
            return
        label = "Star Wish" if wish_type == "star" else "Wishes"
        lines = [
            f"{'⭐ ' if wish_type == 'star' else ''}• `{wish['player_id']}` {wish['name']} — "
            f"{wish['club_name'] or 'clube desconhecido'}"
            for wish in wishes
        ]
        embed = discord.Embed(
            title=f"{label} de {context.author.display_name}",
            description="\n".join(lines),
            color=discord.Color.gold() if wish_type == "star" else discord.Color.blurple(),
        )
        await context.reply(embed=embed, mention_author=False)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(EconomyCog(bot))
