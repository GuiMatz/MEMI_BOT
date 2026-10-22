"""Tudo que muda os limites de uma conta: loja de upgrades, torre, crates e cosméticos.

A loja (`mm!upgrade`) é a base e a torre (`mm!construir`) é o extra: quando os dois dão o
mesmo efeito (wish, Star Wish, daily mais rápido…), os efeitos somam. Os prêmios
permanentes das crates ficam em `user_bonuses` e também somam.

Nada aqui mexe na sorte: a roleta continua sorteando do mesmo jeito para todo mundo.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import timedelta

# Valores-base de toda conta.
ROLLS_PER_CYCLE = 10
BASE_WISHES = 5
BASE_STAR_WISHES = 1
BASE_EXCLUSIVE_SECONDS = 5
BASE_DAILY_COOLDOWN = timedelta(hours=24)
MIN_DAILY_COOLDOWN = timedelta(hours=8)
DAILY_MIN_AMOUNT = 100
DAILY_BASE_CAP = 300
COSMETIC_DAILY_BONUS = 100
CRATE_BASE_PRICE = 500
CRATE_MIN_PRICE = 250
BOX_OFFICE_BASE_CAP = 1_000
INCOME_BASE = 100


@dataclass(frozen=True)
class UpgradeSpec:
    name: str
    effect: str
    limit: int
    base_cost: int
    step_cost: int
    color: str
    fallback_emoji: str

    def cost(self, level: int) -> int:
        return self.base_cost + self.step_cost * level


# Loja V2: ~10 melhorias com limite. Nenhuma aumenta a chance de tirar alguém.
# A redução do preço da crate não está aqui: ela só sai nas próprias crates.
UPGRADES: dict[str, UpgradeSpec] = {
    "rolls": UpgradeSpec(
        "Contratar Olheiro", "+1 roll permanente por ciclo", 10, 2_000, 1_000, "#3B82F6", "🔵"
    ),
    "wishes": UpgradeSpec("Observar Jogador", "+1 slot de wish", 10, 1_000, 500, "#22C55E", "🟢"),
    "star_wishes": UpgradeSpec(
        "Declarar Interesse", "+1 slot de Star Wish", 5, 4_000, 2_000, "#EAB308", "🟡"
    ),
    "daily": UpgradeSpec(
        "Adiantar o Salário", "-10 min no cooldown do daily", 12, 1_000, 250, "#F97316", "🟠"
    ),
    "wish_protection": UpgradeSpec(
        "Prioridade na Negociação",
        "+1 s de proteção quando uma wish sua é rolada",
        10,
        1_500,
        750,
        "#EF4444",
        "🔴",
    ),
    "daily_max": UpgradeSpec(
        "Incorporação Financeira", "+100 no máximo do daily", 5, 2_500, 1_500, "#A855F7", "🟣"
    ),
    "exclusivity": UpgradeSpec(
        "Agente Exclusivo",
        "+1 s só seu para contratar o que você rolou",
        10,
        1_500,
        750,
        "#14B8A6",
        "🩵",
    ),
    "dismiss_refund": UpgradeSpec(
        "Seguro de Contrato",
        "+10% no reembolso do mm!demitir",
        5,
        1_000,
        500,
        "#64748B",
        "⚪",
    ),
    "claim_reset": UpgradeSpec(
        "Renovação de Contrato",
        "libera o mm!rt: zera o claim na hora, a cada 12 h",
        1,
        12_000,
        0,
        "#EC4899",
        "🩷",
    ),
    "box_office_cap": UpgradeSpec(
        "Ampliar a Bilheteria",
        "+250 no limite da bilheteria (andar 5)",
        4,
        1_500,
        1_000,
        "#92400E",
        "🟤",
    ),
    "match_income": UpgradeSpec(
        "Patrocínio Master", "+25 na renda do amistoso", 4, 1_500, 1_000, "#0EA5E9", "🔷"
    ),
}

# Os 12 andares do Centro de Treinamento (efeito curto, para o menu).
TOWER_FLOORS: dict[int, str] = {
    1: "+2 slots de wish",
    2: "5% de chance de ganhar 2 bolas ao rolar um jogador seu",
    3: "Memilets iguais ao overall de cada contratação",
    4: "-25 Memilets no preço da crate",
    5: "Bilheteria: 50 por jogo do campeonato e 100 por vitória",
    6: "+50 na renda do amistoso diário",
    7: "+1 roll por ciclo",
    8: "+1 slot de Star Wish",
    9: "-1 h no cooldown do daily",
    10: "+1 s de proteção de wish",
    11: "+100 no máximo do daily",
    12: "+1 bola em um jogador aleatório dos 11 escalados",
}
# Rótulo curto de cada andar, para a imagem da torre.
TOWER_SHORT: dict[int, str] = {
    1: "+2 wishes",
    2: "Bola em dobro",
    3: "Memilets por contratação",
    4: "Crate -25",
    5: "Bilheteria",
    6: "Renda +50",
    7: "+1 roll",
    8: "+1 Star Wish",
    9: "Daily -1 h",
    10: "Proteção de wish +1 s",
    11: "Daily máx. +100",
    12: "+1 bola no time",
}
TOWER_FIRST_PRICE = 1_000
TOWER_PRICE_STEP = 500

# Prêmios permanentes das crates (user_bonuses) e o teto de cada um.
BONUS_LIMITS = {
    "rolls": 5,
    "wishes": 10,
    "star_wishes": 5,
    "daily_minutes": 6 * 60,
    "wish_protection": 10,
    "crate_discount": 200,
}

# Cosméticos que somam +100 ao máximo do daily quando equipados (coluna de game_teams).
DAILY_COSMETIC_COLUMNS = ("logo_path", "favorite_league", "kit_path", "mascot", "coach_name")


@dataclass(frozen=True)
class Perks:
    upgrades: dict[str, int]
    bonuses: dict[str, int]
    floors: dict[int, int]
    equipped_cosmetics: int

    def floor(self, number: int) -> int:
        return self.floors.get(number, 0)

    @property
    def roll_capacity(self) -> int:
        return ROLLS_PER_CYCLE + self.upgrades["rolls"] + self.bonuses["rolls"] + self.floor(7)

    @property
    def wish_capacity(self) -> int:
        return BASE_WISHES + self.upgrades["wishes"] + self.bonuses["wishes"] + 2 * self.floor(1)

    @property
    def star_wish_capacity(self) -> int:
        return (
            BASE_STAR_WISHES
            + self.upgrades["star_wishes"]
            + self.bonuses["star_wishes"]
            + self.floor(8)
        )

    @property
    def daily_cooldown(self) -> timedelta:
        reduction = timedelta(
            minutes=10 * self.upgrades["daily"] + self.bonuses["daily_minutes"] + 60 * self.floor(9)
        )
        return max(MIN_DAILY_COOLDOWN, BASE_DAILY_COOLDOWN - reduction)

    @property
    def daily_cap(self) -> int:
        return (
            DAILY_BASE_CAP
            + COSMETIC_DAILY_BONUS * self.equipped_cosmetics
            + 100 * self.upgrades["daily_max"]
            + 100 * self.floor(11)
        )

    @property
    def wish_protection_seconds(self) -> int:
        return self.upgrades["wish_protection"] + self.bonuses["wish_protection"] + self.floor(10)

    @property
    def exclusive_seconds(self) -> int:
        return BASE_EXCLUSIVE_SECONDS + self.upgrades["exclusivity"]

    @property
    def dismiss_refund_multiplier(self) -> float:
        return 1 + 0.1 * self.upgrades["dismiss_refund"]

    @property
    def crate_price(self) -> int:
        discount = self.bonuses["crate_discount"] + 25 * self.floor(4)
        return max(CRATE_MIN_PRICE, CRATE_BASE_PRICE - discount)

    @property
    def box_office_cap(self) -> int:
        return BOX_OFFICE_BASE_CAP + 250 * self.upgrades["box_office_cap"]

    @property
    def income_bonus(self) -> int:
        return 25 * self.upgrades["match_income"] + 50 * self.floor(6)

    @property
    def double_ball_chance(self) -> float:
        return min(1.0, 0.05 * self.floor(2))


def load_perks(connection: sqlite3.Connection, discord_user_id: int | str) -> Perks:
    """Lê upgrades, bônus de crate, andares e cosméticos equipados numa conexão aberta."""
    user_id = str(discord_user_id)
    upgrades = {key: 0 for key in UPGRADES}
    for row in connection.execute(
        "SELECT upgrade_key, level FROM user_upgrades WHERE discord_user_id = ?", (user_id,)
    ):
        if row["upgrade_key"] in upgrades:
            upgrades[row["upgrade_key"]] = int(row["level"])
    bonuses = {key: 0 for key in BONUS_LIMITS}
    for row in connection.execute(
        "SELECT bonus_key, amount FROM user_bonuses WHERE discord_user_id = ?", (user_id,)
    ):
        if row["bonus_key"] in bonuses:
            bonuses[row["bonus_key"]] = int(row["amount"])
    floors = {
        int(row["floor"]): int(row["times"])
        for row in connection.execute(
            "SELECT floor, times FROM tower_floors WHERE discord_user_id = ?", (user_id,)
        )
    }
    team = connection.execute(
        f"SELECT {', '.join(DAILY_COSMETIC_COLUMNS)} FROM game_teams WHERE discord_user_id = ?",
        (user_id,),
    ).fetchone()
    equipped = sum(1 for column in DAILY_COSMETIC_COLUMNS if team and team[column])
    return Perks(upgrades, bonuses, floors, equipped)


def add_bonus(
    connection: sqlite3.Connection, discord_user_id: int | str, key: str, amount: int
) -> None:
    if key not in BONUS_LIMITS:
        raise ValueError(f"Bônus desconhecido: {key}")
    connection.execute(
        """
        INSERT INTO user_bonuses (discord_user_id, bonus_key, amount) VALUES (?, ?, ?)
        ON CONFLICT(discord_user_id, bonus_key)
        DO UPDATE SET amount = MIN(?, user_bonuses.amount + excluded.amount)
        """,
        (str(discord_user_id), key, min(amount, BONUS_LIMITS[key]), BONUS_LIMITS[key]),
    )
