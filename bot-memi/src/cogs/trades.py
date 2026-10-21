"""Comando de trocas bilaterais de jogadores e Memilets."""

from __future__ import annotations

import re

import discord
from discord.ext import commands

from src.db import DATABASE_PATH, connect
from src.game import list_owned_players, resolve_player
from src.transactions import (
    TransactionError,
    create_trade,
    settle_trade,
)


class TradeView(discord.ui.View):
    def __init__(self, offer_id: int, recipient_id: int, database_path):
        super().__init__(timeout=900)
        self.offer_id = offer_id
        self.recipient_id = recipient_id
        self.database_path = database_path

    async def _respond(
        self,
        interaction: discord.Interaction,
        *,
        accept: bool,
    ) -> None:
        if interaction.user.id != self.recipient_id:
            await interaction.response.send_message(
                "Somente a pessoa convidada pode responder à oferta.",
                ephemeral=True,
            )
            return
        try:
            status = settle_trade(
                self.offer_id,
                interaction.user.id,
                accept,
                self.database_path,
            )
        except TransactionError as error:
            await interaction.response.send_message(str(error), ephemeral=True)
            return
        messages = {
            "accepted": "✅ A troca foi concluída atomicamente.",
            "declined": "A oferta de troca foi recusada.",
            "expired": "A oferta expirou.",
        }
        await interaction.response.edit_message(
            content=messages[status],
            embed=None,
            view=None,
        )
        self.stop()

    @discord.ui.button(label="Aceitar troca", style=discord.ButtonStyle.success)
    async def accept(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await self._respond(interaction, accept=True)

    @discord.ui.button(label="Recusar", style=discord.ButtonStyle.secondary)
    async def decline(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await self._respond(interaction, accept=False)


class TradesCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.database_path = getattr(bot, "database_path", DATABASE_PATH)

    @commands.command(name="troca")
    async def trade(self, context: commands.Context, *, arguments: str) -> None:
        parts = arguments.split()
        if len(parts) == 2 and parts[0].casefold() in {"aceitar", "recusar"}:
            try:
                offer_id = int(parts[1])
                status = settle_trade(
                    offer_id,
                    context.author.id,
                    parts[0].casefold() == "aceitar",
                    self.database_path,
                )
            except (ValueError, TransactionError) as error:
                message = str(error) if isinstance(error, TransactionError) else "ID inválido."
                await context.reply(message, mention_author=False)
                return
            await context.reply(
                "✅ Troca concluída."
                if status == "accepted"
                else "Oferta de troca recusada.",
                mention_author=False,
            )
            return

        if not context.message.mentions:
            await context.reply(
                "Use `mm!troca @pessoa <seu jogador> por <jogador dela> "
                "[seus_memilets] [memilets_dela]`.",
                mention_author=False,
            )
            return
        target = context.message.mentions[0]
        if target.bot:
            await context.reply("Não é possível trocar com um bot.", mention_author=False)
            return
        text = re.sub(r"<@!?\d+>", " ", arguments).strip()
        coins = re.search(r"((?:\s+\d+){1,2})\s*$", text) if " por " in f" {text.casefold()} " else None
        amounts = [int(value) for value in coins.group(1).split()] if coins else []
        if coins:
            text = text[: coins.start()].strip()
        halves = re.split(r"\s+por\s+", text, maxsplit=1, flags=re.IGNORECASE)
        if len(halves) == 2:
            mine, _ = resolve_player(
                halves[0], list_owned_players(context.author.id, self.database_path, limit=5000)
            )
            theirs, _ = resolve_player(
                halves[1], list_owned_players(target.id, self.database_path, limit=5000)
            )
            if mine is None or theirs is None:
                missing = halves[0] if mine is None else halves[1]
                owner = "no seu elenco" if mine is None else f"no elenco de {target.display_name}"
                await context.reply(
                    f"Não identifiquei **{missing[:60]}** {owner}. Use um nome mais completo.",
                    mention_author=False,
                )
                return
            numeric = [int(mine["id"]), int(theirs["id"]), *amounts]
        else:
            # Formato antigo: IDs dos dois jogadores e moedas opcionais.
            numeric = []
            for part in text.split():
                if not part.isdigit():
                    await context.reply(
                        "Use `mm!troca @pessoa <seu jogador> por <jogador dela> [moedas]`.",
                        mention_author=False,
                    )
                    return
                numeric.append(int(part))
        if not 2 <= len(numeric) <= 4 or any(value < 0 for value in numeric):
            await context.reply(
                "Informe os dois jogadores e, opcionalmente, "
                "os Memilets oferecidos por cada lado.",
                mention_author=False,
            )
            return
        proposer_player_id, recipient_player_id = numeric[:2]
        proposer_memilets = numeric[2] if len(numeric) >= 3 else 0
        recipient_memilets = numeric[3] if len(numeric) == 4 else 0
        try:
            offer_id = create_trade(
                context.author.id,
                context.author.display_name,
                target.id,
                target.display_name,
                proposer_player_id,
                recipient_player_id,
                proposer_memilets,
                recipient_memilets,
                self.database_path,
            )
        except TransactionError as error:
            await context.reply(str(error), mention_author=False)
            return
        with connect(self.database_path) as connection:
            offer = connection.execute(
                """
                SELECT own.name AS proposer_name, other.name AS recipient_name
                FROM trade_offers AS t
                JOIN players AS own ON own.id = t.proposer_player_id
                JOIN players AS other ON other.id = t.recipient_player_id
                WHERE t.id = ?
                """,
                (offer_id,),
            ).fetchone()
        embed = discord.Embed(
            title=f"Oferta de troca #{offer_id}",
            description=(
                f"{context.author.mention} oferece **{offer['proposer_name']}** "
                f"e {proposer_memilets} Memilets por **{offer['recipient_name']}** "
                f"e {recipient_memilets} Memilets."
            ),
            color=discord.Color.gold(),
        )
        embed.set_footer(
            text="A oferta expira em 15 minutos; confirme no botão ou com "
            f"`mm!troca aceitar {offer_id}`."
        )
        await context.reply(
            content=target.mention,
            embed=embed,
            view=TradeView(offer_id, target.id, self.database_path),
            mention_author=False,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(TradesCog(bot))
