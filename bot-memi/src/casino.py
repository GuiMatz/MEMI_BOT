"""Cassino (`mm!cassino`): a regra e o dinheiro dos jogos, sem nada de Discord.

Todos os jogos são **justos**: o retorno médio é exatamente 100% do que se aposta (a casa
não leva nada). Dá para ganhar e perder bastante no curto prazo, mas em média o cassino
não cria nem destrói Memilets.

- **Cara ou coroa**: acertou, recebe 2x.
- **Roleta**: 36 números, sem o zero, metade vermelhos e metade pretos. Cor ou par/ímpar
  pagam 2x, dúzia paga 3x e o número certo paga 36x.
- **Caça-níquel**: 3 rolos com 6 símbolos de pesos diferentes; três iguais pagam pela
  raridade do símbolo e dois iguais devolvem uma parte (a tabela fecha em 100%).
- **Minas**: uma grade de 16 casas com bombas escondidas. Cada casa segura multiplica o
  prêmio pela chance exata de ter passado; dá para sacar a qualquer hora.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path

from src.db import DATABASE_PATH, connect
from src.game import register_user

MIN_BET = 100
MAX_BET = 100_000


class CasinoError(Exception):
    """Aposta recusada (valor ou saldo); a mensagem vai para a pessoa."""


def place_bet(
    discord_user_id: int | str,
    display_name: str,
    amount: int,
    database_path: str | Path = DATABASE_PATH,
) -> int:
    """Tira a aposta do saldo (ou recusa); devolve o saldo que sobrou."""
    if not MIN_BET <= amount <= MAX_BET:
        raise CasinoError(f"A aposta vai de {MIN_BET:,} a {MAX_BET:,} Memilets.".replace(",", "."))
    user_id = str(discord_user_id)
    register_user(user_id, display_name, database_path)
    with connect(database_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        balance = connection.execute(
            "SELECT memilets FROM game_users WHERE discord_user_id = ?", (user_id,)
        ).fetchone()["memilets"]
        if balance < amount:
            raise CasinoError(
                f"Saldo insuficiente: você tem {balance:,} Memilets.".replace(",", ".")
            )
        connection.execute(
            "UPDATE game_users SET memilets = memilets - ? WHERE discord_user_id = ?",
            (amount, user_id),
        )
    return balance - amount


def credit(
    discord_user_id: int | str, amount: int, database_path: str | Path = DATABASE_PATH
) -> int:
    """Paga o prêmio; devolve o saldo novo."""
    with connect(database_path) as connection:
        if amount:
            connection.execute(
                "UPDATE game_users SET memilets = memilets + ? WHERE discord_user_id = ?",
                (amount, str(discord_user_id)),
            )
        return connection.execute(
            "SELECT memilets FROM game_users WHERE discord_user_id = ?", (str(discord_user_id),)
        ).fetchone()["memilets"]


# --- Cara ou coroa ----------------------------------------------------------------------

COIN_SIDES = ("cara", "coroa")


def flip_coin(rng) -> str:
    return rng.choice(COIN_SIDES)


# --- Roleta ---------------------------------------------------------------------------

ROULETTE_NUMBERS = 36
# A ordem dos números na roda (alterna as cores, como numa roleta de verdade).
ROULETTE_WHEEL = (
    32, 15, 19, 4, 21, 2, 25, 17, 34, 6, 27, 13, 36, 11, 30, 8, 23, 10,
    5, 24, 16, 33, 1, 20, 14, 31, 9, 22, 18, 29, 7, 28, 12, 35, 3, 26,
)  # fmt: skip
RED_NUMBERS = frozenset(ROULETTE_WHEEL[0::2])
# Aposta -> (rótulo, multiplicador). Todas com retorno de 100%.
ROULETTE_BETS = {
    "vermelho": ("🔴 Vermelho", 2),
    "preto": ("⚫ Preto", 2),
    "par": ("Par", 2),
    "impar": ("Ímpar", 2),
    "duzia1": ("1ª dúzia (1-12)", 3),
    "duzia2": ("2ª dúzia (13-24)", 3),
    "duzia3": ("3ª dúzia (25-36)", 3),
    "numero": ("Número exato", 36),
}


def spin_roulette(rng) -> int:
    return rng.randint(1, ROULETTE_NUMBERS)


def roulette_wins(bet: str, number: int, chosen: int | None = None) -> bool:
    if bet == "vermelho":
        return number in RED_NUMBERS
    if bet == "preto":
        return number not in RED_NUMBERS
    if bet == "par":
        return number % 2 == 0
    if bet == "impar":
        return number % 2 == 1
    if bet.startswith("duzia"):
        dozen = int(bet[-1])
        return 12 * (dozen - 1) < number <= 12 * dozen
    if bet == "numero":
        return number == chosen
    raise ValueError("Aposta de roleta inválida.")


# --- Caça-níquel ----------------------------------------------------------------------

# Símbolo -> peso no rolo (o mesmo nos três) e quanto paga com três iguais.
SLOT_SYMBOLS = {
    "cereja": (6, 4),
    "limao": (5, 6),
    "sino": (4, 12),
    "estrela": (3, 25),
    "diamante": (2, 60),
    "sete": (1, 250),
}
SLOT_TOTAL_WEIGHT = sum(weight for weight, _ in SLOT_SYMBOLS.values())


def _pair_multiplier() -> Fraction:
    """Quanto dois iguais pagam para o retorno médio fechar em exatamente 100%."""
    total = Fraction(SLOT_TOTAL_WEIGHT)
    triples = sum((Fraction(w) / total) ** 3 * pay for w, pay in SLOT_SYMBOLS.values())
    pairs = sum(
        3 * (Fraction(w) / total) ** 2 * (1 - Fraction(w) / total) for w, _ in SLOT_SYMBOLS.values()
    )
    return (1 - triples) / pairs


SLOT_PAIR_MULTIPLIER = _pair_multiplier()


def spin_slots(rng) -> tuple[str, str, str]:
    symbols = list(SLOT_SYMBOLS)
    weights = [SLOT_SYMBOLS[symbol][0] for symbol in symbols]
    return tuple(rng.choices(symbols, weights=weights, k=3))  # type: ignore[return-value]


def slots_multiplier(reels: tuple[str, str, str]) -> Fraction:
    if reels[0] == reels[1] == reels[2]:
        return Fraction(SLOT_SYMBOLS[reels[0]][1])
    if len(set(reels)) == 2:
        return SLOT_PAIR_MULTIPLIER
    return Fraction(0)


def slots_rtp() -> Fraction:
    """Retorno médio do caça-níquel (1 = 100%), conta exata."""
    total = Fraction(SLOT_TOTAL_WEIGHT)
    result = Fraction(0)
    for a, (wa, _) in SLOT_SYMBOLS.items():
        for b, (wb, _) in SLOT_SYMBOLS.items():
            for c, (wc, _) in SLOT_SYMBOLS.items():
                chance = Fraction(wa) * wb * wc / total**3
                result += chance * slots_multiplier((a, b, c))
    return result


# --- Minas ----------------------------------------------------------------------------

MINES_CELLS = 16
MINES_OPTIONS = (1, 3, 5, 8)


@dataclass
class MinesGame:
    mines: int
    bombs: frozenset[int]
    opened: list[int] = field(default_factory=list)
    exploded: bool = False

    @classmethod
    def new(cls, mines: int, rng) -> "MinesGame":
        if mines not in MINES_OPTIONS:
            raise ValueError("Número de minas inválido.")
        return cls(mines, frozenset(rng.sample(range(MINES_CELLS), mines)))

    @property
    def safe_left(self) -> int:
        return MINES_CELLS - self.mines - len(self.opened)

    @property
    def multiplier(self) -> Fraction:
        """Prêmio justo depois das casas abertas: o inverso da chance de ter passado."""
        value = Fraction(1)
        for index in range(len(self.opened)):
            value *= Fraction(MINES_CELLS - index, MINES_CELLS - self.mines - index)
        return value

    def next_multiplier(self) -> Fraction:
        index = len(self.opened)
        return self.multiplier * Fraction(MINES_CELLS - index, MINES_CELLS - self.mines - index)

    def open(self, cell: int) -> bool:
        """Abre a casa; True se era segura."""
        if self.exploded or cell in self.opened or not 0 <= cell < MINES_CELLS:
            raise ValueError("Casa inválida.")
        if cell in self.bombs:
            self.exploded = True
            return False
        self.opened.append(cell)
        return True


def payout(bet: int, multiplier: Fraction | int) -> int:
    """Prêmio em Memilets (arredondado ao inteiro mais próximo)."""
    return round(Fraction(bet) * Fraction(multiplier))


def new_rng() -> random.SystemRandom:
    return random.SystemRandom()
