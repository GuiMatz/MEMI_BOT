"""Interface da disputa de pênaltis (`mm!penaltis @pessoa [aposta]`).

Quem foi desafiado aceita; aí cada cobrança é uma mensagem com três botões de canto. O
batedor e o goleiro escolhem em segredo (a resposta do clique só a pessoa vê) e, com os
dois escolhidos, a cobrança sai. Quem demora mais que `KICK_SECONDS` tem o canto sorteado.
"""

from __future__ import annotations

import random

import discord

from src import icons
from src.penalty_game import CORNERS, ROUNDS, Shootout
from src.wagers import WagerError, pay, take_stakes

KICK_SECONDS = 60
CORNER_EMOJIS = {"esquerda": "⬅️", "meio": "⬆️", "direita": "➡️"}


def _marks(game: Shootout, side: int) -> str:
    """✅❌ das cobranças do lado, com ⚪ nas que faltam das 5."""
    marks = ["✅" if kick.goal else "❌" for kick in game.kicks if kick.shooter == side]
    return " ".join(marks + ["⚪"] * max(0, ROUNDS - len(marks)))


def shootout_embed(
    game: Shootout, names: tuple[str, str], wager: int, last: str = ""
) -> discord.Embed:
    first, second = game.score()
    embed = discord.Embed(
        title=f"⚽ Pênaltis · {names[0]} × {names[1]}",
        color=discord.Color.from_rgb(63, 198, 216),
    )
    lines = [
        f"**{names[0]}** {first} × {second} **{names[1]}**",
        f"{names[0]}: {_marks(game, 0)}",
        f"{names[1]}: {_marks(game, 1)}",
    ]
    if last:
        lines += ["", last]
    if game.finished:
        lines += ["", f"🏆 **{names[game.winner]}** venceu nos pênaltis!"]
        if wager:
            lines.append(f"{icons.memilets(wager * 2)} Memilets para quem venceu.")
    else:
        number = len(game.kicks) + 1
        sudden = " · morte súbita" if game.taken(game.shooter) >= ROUNDS else ""
        lines += [
            "",
            f"🥅 **Cobrança {number}{sudden}:** **{names[game.shooter]}** bate, "
            f"**{names[game.keeper]}** defende. Escolham o canto (só você vê o seu).",
        ]
    embed.description = "\n".join(lines)
    if wager:
        embed.set_footer(
            text=f"Aposta: {wager:,} cada · quem vence leva {wager * 2:,}".replace(",", ".")
        )
    return embed


def kick_text(names: tuple[str, str], kick) -> str:
    shooter, keeper = names[kick.shooter], names[1 - kick.shooter]
    where = f"{CORNER_EMOJIS[kick.shot]} {kick.shot}"
    if kick.goal:
        result = "**GOL!**"
    elif kick.post:
        result = "**na trave!**"
    else:
        result = "**DEFENDEU!**"
    return (
        f"{shooter} bateu na {where}, {keeper} pulou na "
        f"{CORNER_EMOJIS[kick.dive]} {kick.dive}: {result}"
    )


class KickView(discord.ui.View):
    """Uma cobrança: os dois escolhem o canto; com as duas escolhas, a cobrança sai."""

    def __init__(self, match: "PenaltyMatch"):
        super().__init__(timeout=KICK_SECONDS)
        self.match = match
        self.choices: dict[int, str] = {}
        self.done = False
        for corner in CORNERS:
            button = discord.ui.Button(
                label=corner.capitalize(),
                emoji=CORNER_EMOJIS[corner],
                style=discord.ButtonStyle.secondary,
            )
            button.callback = self._chooser(corner)
            self.add_item(button)

    def _chooser(self, corner: str):
        async def choose(interaction: discord.Interaction) -> None:
            side = self.match.side_of(interaction.user.id)
            if side is None:
                await interaction.response.send_message(
                    "Essa disputa é só dos dois jogadores.", ephemeral=True
                )
                return
            if self.done or side in self.choices:
                await interaction.response.send_message(
                    "Você já escolheu nesta cobrança.", ephemeral=True
                )
                return
            self.choices[side] = corner
            role = "bater" if side == self.match.game.shooter else "pular"
            await interaction.response.send_message(
                f"{CORNER_EMOJIS[corner]} Você vai {role} na **{corner}**.", ephemeral=True
            )
            if len(self.choices) == 2:
                await self._resolve()

        return choose

    async def _resolve(self) -> None:
        if self.done:
            return
        self.done = True
        self.stop()
        game = self.match.game
        shot = self.choices.get(game.shooter) or random.choice(CORNERS)
        dive = self.choices.get(game.keeper) or random.choice(CORNERS)
        kick = game.kick(shot, dive, self.match.rng)
        await self.match.next_kick(kick_text(self.match.names, kick))

    async def on_timeout(self) -> None:
        # Quem não escolheu a tempo tem o canto sorteado.
        await self._resolve()


class PenaltyMatch:
    """Uma disputa em andamento: placar, nomes, aposta e a mensagem que ela atualiza."""

    def __init__(self, players, names, wager: int, database_path, message, rng=None):
        self.players = players  # (id do desafiante, id do desafiado)
        self.names = names
        self.wager = wager
        self.database_path = database_path
        self.message = message
        self.rng = rng or random.SystemRandom()
        self.game = Shootout((players[0], players[1]))

    def side_of(self, user_id: int) -> int | None:
        return self.players.index(user_id) if user_id in self.players else None

    async def start(self) -> None:
        await self.message.edit(
            content=None,
            embed=shootout_embed(self.game, self.names, self.wager),
            view=KickView(self),
        )

    async def next_kick(self, last: str) -> None:
        embed = shootout_embed(self.game, self.names, self.wager, last)
        if self.game.finished:
            if self.wager:
                pay(self.players[self.game.winner], self.wager * 2, self.database_path)
            await self.message.edit(embed=embed, view=None)
            return
        await self.message.edit(embed=embed, view=KickView(self))


class PenaltyChallengeView(discord.ui.View):
    """O desafiado aceita (as apostas saem aqui) ou recusa."""

    def __init__(self, challenger, opponent, wager: int, database_path):
        super().__init__(timeout=120)
        self.challenger = challenger
        self.opponent = opponent
        self.wager = wager
        self.database_path = database_path
        self.message: discord.Message | None = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.opponent.id:
            await interaction.response.send_message(
                "Só quem foi desafiado responde.", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="Aceitar", emoji="⚽", style=discord.ButtonStyle.success)
    async def accept(self, interaction: discord.Interaction, button) -> None:
        if self.wager:
            try:
                take_stakes(
                    (self.challenger.id, self.challenger.display_name),
                    (self.opponent.id, self.opponent.display_name),
                    self.wager,
                    self.database_path,
                )
            except WagerError as error:
                await interaction.response.edit_message(content=str(error), embed=None, view=None)
                self.stop()
                return
        self.stop()
        await interaction.response.defer()
        match = PenaltyMatch(
            (self.challenger.id, self.opponent.id),
            (self.challenger.display_name, self.opponent.display_name),
            self.wager,
            self.database_path,
            interaction.message,
        )
        await match.start()

    @discord.ui.button(label="Recusar", style=discord.ButtonStyle.secondary)
    async def refuse(self, interaction: discord.Interaction, button) -> None:
        self.stop()
        await interaction.response.edit_message(
            content=f"{self.opponent.display_name} recusou os pênaltis.", embed=None, view=None
        )

    async def on_timeout(self) -> None:
        if self.message:
            try:
                await self.message.edit(content="Desafio de pênaltis expirou.", view=None)
            except discord.HTTPException:
                pass
