"""Comando de rolagem e botões de claim."""

from __future__ import annotations

from pathlib import Path

import discord
from discord.ext import commands

from src.clock import brasilia_time
from src.cogs.common import card_file
from src.db import DATABASE_PATH
from src.game import register_user
from src.rolls import (
    NoEligiblePlayers,
    RollLimitReached,
    roll_player,
    claim_roll_offer,
)
from src.ratings import rarity_label


class RollOfferView(discord.ui.View):
    def __init__(
        self,
        offer_id: int,
        database_path: str | Path,
        *,
        duplicate_reward: bool,
        player_name: str,
    ):
        super().__init__(timeout=300)
        self.offer_id = offer_id
        self.database_path = database_path
        self.player_name = player_name
        button = discord.ui.Button(
            label="Resgatar Memilets" if duplicate_reward else "Reivindicar",
            style=(
                discord.ButtonStyle.primary
                if duplicate_reward
                else discord.ButtonStyle.success
            ),
            emoji="🪙" if duplicate_reward else None,
        )
        button.callback = self._claim
        self.add_item(button)

    async def _claim(self, interaction: discord.Interaction) -> None:
        register_user(
            interaction.user.id,
            interaction.user.display_name,
            self.database_path,
        )
        result = claim_roll_offer(
            self.offer_id,
            interaction.user.id,
            self.database_path,
        )
        if result.status == "player_claimed":
            await interaction.response.send_message(
                f"✅ {interaction.user.mention} contratou **{self.player_name}**.",
            )
            self.stop()
            return
        if result.status == "reward_claimed":
            await interaction.response.edit_message(
                content=(
                    f"🪙 {interaction.user.mention} resgatou "
                    f"**{result.reward_amount} Memilets**."
                ),
                embed=None,
                view=None,
            )
            self.stop()
            return

        messages = {
            "exclusive": "A janela exclusiva do autor do roll ainda está aberta.",
            "not_roller": "A recompensa dessa rolagem só pode ser resgatada por quem rolou.",
            "already_claimed": "Essa recompensa já foi resgatada ou o jogador já foi reivindicado.",
            "expired": "Essa oportunidade de claim expirou.",
            "unavailable": "Outra pessoa já reivindicou esse jogador.",
            "not_found": "Essa oportunidade de claim não está mais disponível.",
        }
        if result.status == "cooldown" and result.retry_at:
            message = (
                "Seu próximo claim libera às "
                f"**{brasilia_time(result.retry_at)}**."
            )
        else:
            message = messages.get(
                result.status,
                "Não foi possível concluir o claim.",
            )
        await interaction.response.send_message(message, ephemeral=True)


class RollsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.database_path = getattr(bot, "database_path", DATABASE_PATH)

    @commands.command(name="roll")
    async def roll(self, context: commands.Context) -> None:
        try:
            result = roll_player(
                context.author.id,
                context.author.display_name,
                self.database_path,
            )
        except RollLimitReached as error:
            await context.reply(
                f"Você usou os {error.capacity} rolls deste ciclo. "
                "Eles renovam às "
                f"**{brasilia_time(error.reset_at)}**.",
                mention_author=False,
            )
            return
        except NoEligiblePlayers:
            await context.reply(
                "Não há jogadores elegíveis na roleta neste momento.",
                mention_author=False,
            )
            return

        player = result.player
        overall = result.effective_overall or player["overall"]
        embed = discord.Embed(
            title=player["display_name"] or player["name"],
            color=discord.Color.gold() if overall >= 90 else discord.Color.blurple(),
        )
        club = player["club_name"] or "Passe livre"
        embed.description = (
            f"**{overall}** · {rarity_label(overall)} · {player['game_position']} · {club}"
        )
        embed.set_footer(
            text=f"Rolls restantes neste ciclo: {result.rolls_remaining}"
        )

        view = None
        if result.kind == "unowned":
            if result.offer_id is None:
                raise RuntimeError("Roll livre sem oferta de claim.")
            view = RollOfferView(
                result.offer_id,
                self.database_path,
                duplicate_reward=False,
                player_name=player["display_name"] or player["name"],
            )
        elif result.kind == "own_duplicate_ball":
            embed.description += f"\nVocê ganhou 1 bola ({result.balls}/10) e 100 Memilets."
        elif result.kind == "own_duplicate_max_balls":
            embed.description += "\nLimite de bolas atingido: +500 Memilets."
        else:
            if result.offer_id is None:
                raise RuntimeError("Roll duplicado sem oferta de recompensa.")
            embed.description += f"\nDuplicata: resgate {result.reward_amount} Memilets."
            view = RollOfferView(
                result.offer_id,
                self.database_path,
                duplicate_reward=True,
                player_name=player["display_name"] or player["name"],
            )

        image = await card_file({**player, "effective_overall": overall})
        embed.set_image(url=f"attachment://{image.filename}")
        await context.reply(
            embed=embed,
            file=image,
            view=view,
            mention_author=False,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(RollsCog(bot))
