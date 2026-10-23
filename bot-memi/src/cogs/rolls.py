"""Comando de rolagem e botão de claim, em Components V2.

A carta sai numa moldura cuja linha lateral tem a cor da série de raridade. Embaixo
da carta fica o botão e, como no Mudae, o aviso "⚠️ 2 ROLLS RESTANTES ⚠️" quando
faltam dois; sem rolls, a moldura diz quando eles renovam. Depois do claim o botão
some e a moldura mostra quem contratou.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import discord
from discord.ext import commands

from src import icons
from src.accents import rarity_color
from src.clock import brasilia_time
from src.cogs.common import card_file, followup_sender
from src.db import DATABASE_PATH
from src.economy import EconomyError, ROLLS_PER_BONUS_CLAIM, activate_rolls, get_account_status
from src.game import register_user
from src.teams import equip_if_better, upgrade_slot
from src.rolls import (
    NoEligiblePlayers,
    RollLimitReached,
    RollOutcome,
    roll_player,
    claim_roll_offer,
)
from src.ratings import rarity_label

ROLLS_WARNING_AT = 2

CLAIM_ERRORS = {
    "exclusive": "A janela exclusiva do autor do roll ainda está aberta.",
    "not_roller": "A recompensa dessa rolagem só pode ser resgatada por quem rolou.",
    "already_claimed": "Essa recompensa já foi resgatada ou o jogador já foi reivindicado.",
    "expired": "Essa oportunidade de claim expirou.",
    "unavailable": "Outra pessoa já reivindicou esse jogador.",
    "not_found": "Essa oportunidade de claim não está mais disponível.",
}


def rolls_line(remaining: int, resets_at) -> str:
    """Rodapé da moldura: aviso grande com 2 rolls, horário de renovação com 0."""
    if remaining == ROLLS_WARNING_AT:
        return f"**⚠️ {ROLLS_WARNING_AT} ROLLS RESTANTES ⚠️**"
    if remaining <= 0:
        return f"**⛔ SEUS ROLLS ACABARAM** · renovam às **{brasilia_time(resets_at)}**"
    return f"-# 🎲 {remaining} roll{'s' if remaining > 1 else ''} restante{'s' if remaining > 1 else ''}"


def upgrade_line(upgrade: dict | None) -> str | None:
    """Aviso de que o jogador rolado é melhor que um titular de quem rolou."""
    if not upgrade:
        return None
    position = upgrade["position"]
    current = upgrade["replaces"]
    if current is None:
        return f"⬆️ **Sua vaga de {position} está vazia**: contratando, ele já entra no time."
    name = current.get("display_name") or current["name"]
    return (
        f"⬆️ **Melhor que o seu titular de {position}** ({name} · {upgrade['old']} → "
        f"{upgrade['new']}): contratando, ele já entra no time."
    )


def roll_details(result: RollOutcome, overall: int, upgrade: dict | None = None) -> str:
    player = result.player
    club = player["club_name"] or "Passe livre"
    lines = [f"**{overall}** · {rarity_label(overall)} · {player['game_position']} · {club}"]
    if line := upgrade_line(upgrade):
        lines.append(line)
    if result.kind == "own_duplicate_ball":
        lines.append(f"Você ganhou 1 bola ({result.balls}/10) e 100 Memilets.")
    elif result.kind == "own_duplicate_double_ball":
        lines.append(f"Centro de Treinamento: **2 bolas** ({result.balls}/10) e 100 Memilets.")
    elif result.kind == "own_duplicate_max_balls":
        lines.append("Limite de bolas atingido: +500 Memilets.")
    elif result.kind != "unowned":
        lines.append(f"Duplicata: resgate {result.reward_amount} Memilets.")
    return "\n".join(lines)


class RollLayout(discord.ui.LayoutView):
    """A carta rolada, com o botão de claim (ou de resgate) dentro da moldura."""

    def __init__(
        self,
        result: RollOutcome,
        overall: int,
        image_name: str,
        database_path: str | Path,
        roller_id: int | None = None,
        reroll=None,
        upgrade: dict | None = None,
    ):
        super().__init__(timeout=300)
        self.result = result
        self.roller_id = roller_id
        self.reroll_button: discord.ui.Button | None = None
        if reroll is not None:
            # Rolar de novo sem digitar `mm!roll` (só quem rolou).
            self.reroll_button = discord.ui.Button(
                label="Rolar de novo", emoji="🎲", style=discord.ButtonStyle.secondary
            )
            self.reroll_button.callback = self._reroll
            self._reroll_callback = reroll
        self.upgrade = upgrade
        self.overall = overall
        self.image_name = image_name
        self.database_path = database_path
        self.player_name = result.player["display_name"] or result.player["name"]
        self.status: str | None = None
        self.message: discord.Message | None = None
        self.button: discord.ui.Button | None = None
        if result.offer_id is not None:
            reward = result.kind != "unowned"
            self.button = discord.ui.Button(
                label="Resgatar Memilets" if reward else "Reivindicar",
                style=discord.ButtonStyle.primary if reward else discord.ButtonStyle.success,
                emoji="🪙" if reward else "✍️",
            )
            self.button.callback = self._claim
        self._build()

    def _build(self) -> None:
        self.clear_items()
        items: list[discord.ui.Item[Any]] = []
        if self.result.wished_by:
            mentions = " ".join(f"<@{user_id}>" for user_id in self.result.wished_by[:10])
            wish = f"💫 Na wish de {mentions}"
            if self.result.protection_seconds:
                wish += f" · protegido por {self.result.protection_seconds} s"
            items.append(discord.ui.TextDisplay(wish))
        items.append(
            discord.ui.TextDisplay(
                f"## {self.player_name}\n{roll_details(self.result, self.overall, None if self.status else self.upgrade)}"
            )
        )
        items.append(
            discord.ui.MediaGallery(discord.MediaGalleryItem(f"attachment://{self.image_name}"))
        )
        buttons = [self.button] if self.button is not None and not self.status else []
        if self.reroll_button is not None:
            buttons.append(self.reroll_button)
        if self.status:
            items.append(discord.ui.TextDisplay(self.status))
        if buttons:
            items.append(discord.ui.ActionRow(*buttons))
        items.append(
            discord.ui.TextDisplay(
                rolls_line(self.result.rolls_remaining, self.result.cycle_resets_at)
            )
        )
        self.add_item(discord.ui.Container(*items, accent_colour=rarity_color(self.overall)))

    async def _claim(self, interaction: discord.Interaction) -> None:
        register_user(interaction.user.id, interaction.user.display_name, self.database_path)
        outcome = claim_roll_offer(self.result.offer_id, interaction.user.id, self.database_path)
        if outcome.status == "player_claimed":
            self.status = f"✅ Contratado por {interaction.user.mention}"
            self._build()
            self.stop()
            await interaction.response.edit_message(view=self)
            # Melhor que um titular de quem contratou: já entra no time.
            equipped = await asyncio.to_thread(
                equip_if_better,
                interaction.user.id,
                int(self.result.player["id"]),
                self.database_path,
            )
            equipped_text = ""
            if equipped:
                out = equipped["replaces"]
                equipped_text = f"\n⬆️ Entrou no time como **{equipped['position']}**" + (
                    f" no lugar de {out.get('display_name') or out['name']}." if out else "."
                )
            bonus = (
                f" Centro de Treinamento: +{icons.memilets(outcome.reward_amount)} Memilets."
                if outcome.reward_amount
                else ""
            )
            await interaction.followup.send(
                f"✅ {interaction.user.mention} contratou **{self.player_name}**.{bonus}"
                + equipped_text
            )
            return
        if outcome.status == "reward_claimed":
            self.status = (
                f"{icons.memilets()} {interaction.user.mention} resgatou "
                f"{icons.memilets(outcome.reward_amount)} Memilets."
            )
            self._build()
            self.stop()
            await interaction.response.edit_message(view=self)
            return
        if outcome.status == "wish_protected" and outcome.protected_until:
            message = (
                "Esse jogador está na wish de alguém e protegido até "
                f"<t:{int(outcome.protected_until.timestamp())}:T>."
            )
        elif outcome.status == "cooldown" and outcome.retry_at:
            message = f"Seu próximo claim libera às **{brasilia_time(outcome.retry_at)}**."
        else:
            message = CLAIM_ERRORS.get(outcome.status, "Não foi possível concluir o claim.")
        await interaction.response.send_message(message, ephemeral=True)

    async def _reroll(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.roller_id:
            await interaction.response.send_message(
                "Esse botão é de quem rolou. Use `mm!roll` para rolar.", ephemeral=True
            )
            return
        await self._reroll_callback(interaction)

    async def on_timeout(self) -> None:
        if self.message is None or (self.reroll_button is None and self.button is None):
            return
        # Os botões param de funcionar: some o de rolar de novo e o claim encerra.
        self.reroll_button = None
        if self.button is not None and not self.status:
            self.status = "-# ⌛ Tempo de claim encerrado."
        self._build()
        try:
            await self.message.edit(view=self)
        except discord.HTTPException:
            pass


class ActivateRollsView(discord.ui.View):
    """No aviso de fim dos rolls: ativa os guardados e já rola de novo."""

    def __init__(self, cog: "RollsCog", owner, stored: int):
        super().__init__(timeout=300)
        self.cog = cog
        self.owner = owner
        self.amount = min(stored, ROLLS_PER_BONUS_CLAIM)
        button = discord.ui.Button(
            label=f"Ativar {self.amount} e rolar",
            emoji="🎲",
            style=discord.ButtonStyle.success,
        )
        button.callback = self._activate
        self.add_item(button)

    async def _activate(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.owner.id:
            await interaction.response.send_message(
                "Esse botão é de quem rolou. Use `mm!ativar <quantidade>`.", ephemeral=True
            )
            return
        try:
            result = activate_rolls(
                interaction.user.id,
                interaction.user.display_name,
                self.cog.database_path,
                self.amount,
            )
        except EconomyError as error:
            await interaction.response.send_message(str(error), ephemeral=True)
            return
        text = f"🎲 {result.activated} rolls ativados."
        if result.claims_gained:
            text += f" ✅ +{result.claims_gained} contratação extra."
        await interaction.response.edit_message(content=text, view=None)
        await self.cog.send_roll(interaction.user, followup_sender(interaction))


class RollsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.database_path = getattr(bot, "database_path", DATABASE_PATH)

    @commands.command(name="roll", aliases=["obter", "rolls"])
    async def roll(self, context: commands.Context) -> None:
        await self.send_roll(
            context.author, lambda **kwargs: context.reply(mention_author=False, **kwargs)
        )

    async def roll_from_button(self, interaction: discord.Interaction) -> None:
        """Botões "Rolar" (no roll e no `mm!tu`): a resposta vem como mensagem nova."""
        await interaction.response.defer(thinking=True)
        await self.send_roll(interaction.user, followup_sender(interaction))

    async def send_roll(self, user, send) -> None:
        """Rola para `user` e manda o resultado com `send(**kwargs)` (devolve a mensagem)."""
        try:
            # Fora do loop principal: o roll não trava os outros comandos enquanto roda.
            result = await asyncio.to_thread(
                roll_player,
                user.id,
                user.display_name,
                self.database_path,
            )
        except RollLimitReached as error:
            stored = get_account_status(user.id, user.display_name, self.database_path).roll_tokens
            view = ActivateRollsView(self, user, stored) if stored else None
            await send(
                content=f"Você usou os {error.capacity} rolls deste ciclo. "
                "Eles renovam às "
                f"**{brasilia_time(error.reset_at)}**."
                + (
                    f"\n📦 Você tem **{stored}** rolls guardados: ative com "
                    "`mm!ativar <quantidade>` ou no botão."
                    if stored
                    else ""
                ),
                **({"view": view} if view else {}),
            )
            return
        except NoEligiblePlayers:
            await send(content="Não há jogadores elegíveis na roleta neste momento.")
            return

        if (
            result.kind != "unowned"
            and result.offer_id is None
            and result.kind
            not in {
                "own_duplicate_ball",
                "own_duplicate_double_ball",
                "own_duplicate_max_balls",
            }
        ):
            raise RuntimeError("Roll duplicado sem oferta de recompensa.")
        if result.kind == "unowned" and result.offer_id is None:
            raise RuntimeError("Roll livre sem oferta de claim.")
        overall = result.effective_overall or result.player["overall"]
        image = await card_file({**result.player, "effective_overall": overall})
        upgrade = None
        if result.kind == "unowned":
            upgrade = await asyncio.to_thread(
                upgrade_slot, user.id, result.player["game_position"], overall, self.database_path
            )
        layout = RollLayout(
            result,
            overall,
            image.filename,
            self.database_path,
            roller_id=user.id,
            reroll=self.roll_from_button,
            upgrade=upgrade,
        )
        layout.message = await send(
            view=layout,
            file=image,
            allowed_mentions=discord.AllowedMentions(users=True, replied_user=False),
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(RollsCog(bot))
