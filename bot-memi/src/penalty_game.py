"""Disputa de pênaltis interativa (`mm!penaltis`): a regra, sem nada de Discord.

Cada rodada tem um batedor e um goleiro. Os dois escolhem um canto em segredo (esquerda,
meio ou direita). Goleiro que acerta o canto defende em `SAVE_IF_GUESSED`; errando, o
chute ainda pode ir na trave em `MISS_CHANCE`. São 5 cobranças para cada lado e, empatado,
alternadas até alguém abrir vantagem (morte súbita). A disputa acaba antes quando um lado
não alcança mais o outro.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

CORNERS = ("esquerda", "meio", "direita")
ROUNDS = 5
SAVE_IF_GUESSED = 0.75
MISS_CHANCE = 0.06


@dataclass(frozen=True)
class Kick:
    shooter: int  # 0 ou 1: quem bateu
    shot: str
    dive: str
    goal: bool
    post: bool = False  # errou sozinho (trave/fora)


@dataclass
class Shootout:
    players: tuple[int, int]  # ids; o 0 bate primeiro
    kicks: list[Kick] = field(default_factory=list)

    @property
    def shooter(self) -> int:
        return len(self.kicks) % 2

    @property
    def keeper(self) -> int:
        return 1 - self.shooter

    def score(self) -> tuple[int, int]:
        goals = [0, 0]
        for kick in self.kicks:
            goals[kick.shooter] += kick.goal
        return goals[0], goals[1]

    def taken(self, side: int) -> int:
        return sum(1 for kick in self.kicks if kick.shooter == side)

    @property
    def finished(self) -> bool:
        first, second = self.score()
        taken = (self.taken(0), self.taken(1))
        if max(taken) <= ROUNDS:
            # Nas 5 primeiras: acaba quando um lado não alcança mais o outro.
            left = (ROUNDS - taken[0], ROUNDS - taken[1])
            return first + left[0] < second or second + left[1] < first
        # Morte súbita: depois de cada par de cobranças, quem estiver na frente ganha.
        return taken[0] == taken[1] and first != second

    @property
    def winner(self) -> int | None:
        if not self.finished:
            return None
        first, second = self.score()
        return 0 if first > second else 1

    def kick(self, shot: str, dive: str, rng: random.Random | random.SystemRandom) -> Kick:
        """Resolve a cobrança da vez com os cantos escolhidos."""
        if shot not in CORNERS or dive not in CORNERS:
            raise ValueError("Canto inválido.")
        if self.finished:
            raise ValueError("A disputa já acabou.")
        post = False
        if shot == dive:
            goal = rng.random() >= SAVE_IF_GUESSED
        else:
            post = rng.random() < MISS_CHANCE
            goal = not post
        result = Kick(self.shooter, shot, dive, goal, post)
        self.kicks.append(result)
        return result
