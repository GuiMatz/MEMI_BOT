"""Daily, loja de upgrades (V2), crates (V2) e wishes."""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from src.clock import BRASILIA
from src.cosmetics import COSMETIC_KINDS, grant, random_new_item
from src.db import DATABASE_PATH, connect
from src.game import register_user
from src.missions import record
from src.perks import (
    BONUS_LIMITS,
    CRATE_BASE_PRICE,
    DAILY_MIN_AMOUNT,
    MIN_DAILY_COOLDOWN,
    UPGRADES,
    add_bonus,
    load_perks,
)
from src.rolls import CYCLE_LENGTH, cycle_start_for

DAILY_TIMEZONE = BRASILIA
CRATE_PRICE = CRATE_BASE_PRICE

UPGRADE_LIMITS = {key: spec.limit for key, spec in UPGRADES.items()}
UPGRADE_LABELS = {key: spec.name for key, spec in UPGRADES.items()}

# Crates V2: toda caixa traz 1 item Básico e, com sorte, 1 item de outra raridade.
# As chances são baixas de propósito: tirar algo raro deve ser difícil de verdade.
CRATE_RARITIES = ("basico", "incomum", "raro", "epico", "lendario")
CRATE_EXTRA_CHANCES = {"lendario": 0.005, "epico": 0.015, "raro": 0.05, "incomum": 0.15}
# Memilets que vêm no lugar dos itens de cada raridade (um OU outro, nunca os dois).
# Calibrado para a caixa de Memilets devolver, em média, quase o preço (~480 de 500):
# a caixa comum não parece prejuízo e os itens raros são o lucro.
CRATE_MEMILETS = {
    "basico": (250, 400),
    "incomum": (450, 600),
    "raro": (900, 900),
    "epico": (1_500, 1_500),
    "lendario": (2_500, 2_500),
}
# Garantia: a cada 30 caixas sem Raro (ou melhor), a seguinte traz pelo menos um Raro.
CRATE_PITY_LIMIT = 30
CRATE_BATCHES = (1, 5, 10)
CRATE_ITEM_CHANCE = 0.5
CRATE_DOUBLE_CHANCE = 0.05
CRATE_INSTANT_ROLLS = (1, 3)
CRATE_ITEMS = {
    "incomum": ("cosmetic",),
    "raro": ("daily_1h", "wish_protection"),
    "epico": ("star_wish_slot", "wish_slot", "daily_2h"),
    "lendario": ("permanent_roll", "random_upgrade", "crate_discount"),
}
# Item de crate -> (bônus permanente, quanto soma).
CRATE_BONUS_ITEMS = {
    "daily_1h": ("daily_minutes", 60),
    "daily_2h": ("daily_minutes", 120),
    "wish_protection": ("wish_protection", 1),
    "star_wish_slot": ("star_wishes", 1),
    "wish_slot": ("wishes", 1),
    "permanent_roll": ("rolls", 1),
    "crate_discount": ("crate_discount", 10),
}
RARITY_LABELS = {
    "basico": "Básico",
    "incomum": "Incomum",
    "raro": "Raro",
    "epico": "Épico",
    "lendario": "Lendário",
}


class EconomyError(Exception):
    """Erro de regra da economia que pode ser informado ao jogador."""


class InsufficientFunds(EconomyError):
    """Saldo insuficiente para concluir a operação."""


class DailyCooldown(EconomyError):
    def __init__(self, available_at: datetime):
        self.available_at = available_at
        super().__init__("O daily ainda está em cooldown.")


class UpgradeMaxed(EconomyError):
    """O upgrade já atingiu o nível máximo."""


@dataclass(frozen=True)
class DailyReward:
    amount: int
    available_at: datetime
    cap: int


@dataclass(frozen=True)
class UpgradePurchase:
    key: str
    level: int
    cost: int


@dataclass(frozen=True)
class CratePrize:
    rarity: str
    kind: str
    amount: int = 0
    name: str = ""


@dataclass(frozen=True)
class CrateResult:
    price: int
    prizes: tuple[CratePrize, ...] = field(default_factory=tuple)
    doubled: bool = False
    pity: int = 0
    guaranteed: bool = False

    @property
    def memilets(self) -> int:
        return sum(prize.amount for prize in self.prizes if prize.kind == "memilets")

    @property
    def best_rarity(self) -> str:
        return max(
            (prize.rarity for prize in self.prizes), key=CRATE_RARITIES.index, default="basico"
        )


def _utc_now(now: datetime | None = None) -> datetime:
    value = now or datetime.now(timezone.utc)
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("now deve incluir um fuso horário.")
    return value.astimezone(timezone.utc)


def get_upgrade_levels(
    discord_user_id: int | str,
    database_path: str | Path = DATABASE_PATH,
) -> dict[str, int]:
    with connect(database_path) as connection:
        return dict(load_perks(connection, discord_user_id).upgrades)


def get_upgrade_cost(key: str, current_level: int) -> int:
    if key not in UPGRADES:
        raise ValueError(f"Upgrade desconhecido: {key}")
    if current_level >= UPGRADES[key].limit:
        raise UpgradeMaxed(f"{UPGRADES[key].name} já está no nível máximo.")
    return UPGRADES[key].cost(current_level)


def buy_upgrade(
    discord_user_id: int | str,
    display_name: str,
    key: str,
    database_path: str | Path = DATABASE_PATH,
) -> UpgradePurchase:
    if key not in UPGRADES:
        raise ValueError("Upgrade inválido.")
    user_id = str(discord_user_id)
    register_user(user_id, display_name, database_path)
    with connect(database_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        level = load_perks(connection, user_id).upgrades[key]
        cost = get_upgrade_cost(key, level)
        balance = connection.execute(
            "SELECT memilets FROM game_users WHERE discord_user_id = ?",
            (user_id,),
        ).fetchone()["memilets"]
        if balance < cost:
            raise InsufficientFunds(f"Você precisa de {cost} Memilets para comprar este upgrade.")
        connection.execute(
            """
            UPDATE game_users
            SET memilets = memilets - ?, updated_at = CURRENT_TIMESTAMP
            WHERE discord_user_id = ?
            """,
            (cost, user_id),
        )
        _raise_upgrade(connection, user_id, key)
        return UpgradePurchase(key, level + 1, cost)


def _raise_upgrade(connection, user_id: str, key: str) -> None:
    connection.execute(
        """
        INSERT INTO user_upgrades (discord_user_id, upgrade_key, level)
        VALUES (?, ?, 1)
        ON CONFLICT(discord_user_id, upgrade_key)
        DO UPDATE SET level = user_upgrades.level + 1
        """,
        (user_id, key),
    )


def claim_daily(
    discord_user_id: int | str,
    display_name: str,
    database_path: str | Path = DATABASE_PATH,
    *,
    now: datetime | None = None,
    rng: random.Random | random.SystemRandom | None = None,
) -> DailyReward:
    """Memilets de 100 até o máximo da conta (300 + cosméticos, upgrades e torre)."""
    current_time = _utc_now(now)
    user_id = str(discord_user_id)
    generator = rng or random.SystemRandom()
    register_user(user_id, display_name, database_path)
    with connect(database_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        perks = load_perks(connection, user_id)
        account = connection.execute(
            "SELECT last_daily_at FROM game_users WHERE discord_user_id = ?",
            (user_id,),
        ).fetchone()
        if account["last_daily_at"]:
            available_at = (
                datetime.fromisoformat(account["last_daily_at"]).astimezone(timezone.utc)
                + perks.daily_cooldown
            )
            if current_time < available_at:
                raise DailyCooldown(available_at)
        cap = perks.daily_cap
        amount = generator.randint(DAILY_MIN_AMOUNT, cap)
        connection.execute(
            """
            UPDATE game_users
            SET memilets = memilets + ?, last_daily_at = ?, updated_at = CURRENT_TIMESTAMP
            WHERE discord_user_id = ?
            """,
            (amount, current_time.isoformat(timespec="seconds"), user_id),
        )
        record(connection, user_id, "daily", now=current_time)
        return DailyReward(amount, current_time + perks.daily_cooldown, cap)


def add_wish(
    discord_user_id: int | str,
    player_id: int,
    wish_type: str,
    database_path: str | Path = DATABASE_PATH,
) -> str:
    if wish_type not in {"normal", "star"}:
        raise ValueError("Tipo de wish inválido.")
    user_id = str(discord_user_id)
    with connect(database_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        perks = load_perks(connection, user_id)
        capacity = perks.wish_capacity if wish_type == "normal" else perks.star_wish_capacity
        current = connection.execute(
            """
            SELECT wish_type FROM user_wishes
            WHERE discord_user_id = ? AND player_id = ?
            """,
            (user_id, player_id),
        ).fetchone()
        if current and current["wish_type"] == wish_type:
            connection.execute(
                "DELETE FROM user_wishes WHERE discord_user_id = ? AND player_id = ?",
                (user_id, player_id),
            )
            return "removed"
        used = connection.execute(
            """
            SELECT COUNT(*) AS amount FROM user_wishes
            WHERE discord_user_id = ? AND wish_type = ?
            """,
            (user_id, wish_type),
        ).fetchone()["amount"]
        if int(used) >= capacity:
            raise EconomyError(
                f"Você já está usando todos os {capacity} slots de "
                f"{'wish' if wish_type == 'normal' else 'Star Wish'}."
            )
        if current:
            connection.execute(
                """
                UPDATE user_wishes SET wish_type = ?, created_at = CURRENT_TIMESTAMP
                WHERE discord_user_id = ? AND player_id = ?
                """,
                (wish_type, user_id, player_id),
            )
            return "updated"
        connection.execute(
            """
            INSERT INTO user_wishes (discord_user_id, player_id, wish_type)
            VALUES (?, ?, ?)
            """,
            (user_id, player_id, wish_type),
        )
        return "added"


def list_wishes(
    discord_user_id: int | str,
    database_path: str | Path = DATABASE_PATH,
) -> list[dict[str, Any]]:
    with connect(database_path) as connection:
        rows = connection.execute(
            """
            SELECT w.player_id, w.wish_type,
                   COALESCE(NULLIF(p.display_name, ''), p.name) AS name,
                   p.club_name
            FROM user_wishes AS w
            JOIN players AS p ON p.id = w.player_id
            WHERE w.discord_user_id = ?
            ORDER BY CASE w.wish_type WHEN 'star' THEN 0 ELSE 1 END, name
            """,
            (str(discord_user_id),),
        ).fetchall()
    return [dict(row) for row in rows]


CLAIM_RESET_COOLDOWN = timedelta(hours=12)


def reset_claim(
    discord_user_id: int | str,
    display_name: str,
    database_path: str | Path = DATABASE_PATH,
    *,
    now: datetime | None = None,
) -> datetime:
    """`mm!rt`: zera o claim do ciclo na hora; devolve quando o comando volta."""
    from src.rolls import claimed_at

    current_time = _utc_now(now)
    user_id = str(discord_user_id)
    register_user(user_id, display_name, database_path)
    with connect(database_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        if not load_perks(connection, user_id).upgrades["claim_reset"]:
            raise EconomyError(
                "O `mm!rt` é liberado pelo upgrade **Renovação de Contrato** (`mm!upgrade`)."
            )
        account = connection.execute(
            "SELECT last_claim_at, last_rt_at FROM game_users WHERE discord_user_id = ?",
            (user_id,),
        ).fetchone()
        if account["last_rt_at"]:
            available_at = datetime.fromisoformat(account["last_rt_at"]) + CLAIM_RESET_COOLDOWN
            if current_time < available_at:
                raise DailyCooldown(available_at)
        cycle_start = cycle_start_for(current_time)
        if not account["last_claim_at"] or claimed_at(account["last_claim_at"]) < cycle_start:
            raise EconomyError("Seu claim já está disponível; guarde o `mm!rt` para depois.")
        connection.execute(
            "UPDATE game_users SET last_claim_at = NULL, last_rt_at = ? WHERE discord_user_id = ?",
            (current_time.isoformat(timespec="seconds"), user_id),
        )
        return current_time + CLAIM_RESET_COOLDOWN


def get_crate_pity(discord_user_id: int | str, database_path: str | Path = DATABASE_PATH) -> int:
    """Caixas seguidas sem Raro (a garantia vem em `CRATE_PITY_LIMIT`)."""
    with connect(database_path) as connection:
        row = connection.execute(
            "SELECT crate_pity FROM user_upgrades WHERE discord_user_id = ? "
            "AND upgrade_key = 'rolls'",
            (str(discord_user_id),),
        ).fetchone()
    return int(row["crate_pity"]) if row else 0


def open_crates(
    discord_user_id: int | str,
    display_name: str,
    count: int,
    database_path: str | Path = DATABASE_PATH,
    *,
    rng: random.Random | random.SystemRandom | None = None,
) -> list[CrateResult]:
    """Abre `count` caixas seguidas; confere antes se o saldo paga todas."""
    if count not in CRATE_BATCHES:
        raise ValueError("Quantidade de caixas inválida.")
    register_user(discord_user_id, display_name, database_path)
    price = get_crate_price(discord_user_id, database_path)
    with connect(database_path) as connection:
        balance = connection.execute(
            "SELECT memilets FROM game_users WHERE discord_user_id = ?",
            (str(discord_user_id),),
        ).fetchone()["memilets"]
    if balance < price * count:
        affordable = balance // price
        raise InsufficientFunds(
            f"{count} caixas custam {price * count:,} Memilets".replace(",", ".")
            + (f"; seu saldo paga {affordable}." if affordable else "; seu saldo não paga nenhuma.")
        )
    return [open_crate(discord_user_id, display_name, database_path, rng=rng) for _ in range(count)]


def get_crate_price(discord_user_id: int | str, database_path: str | Path = DATABASE_PATH) -> int:
    with connect(database_path) as connection:
        return load_perks(connection, discord_user_id).crate_price


def open_crate(
    discord_user_id: int | str,
    display_name: str,
    database_path: str | Path = DATABASE_PATH,
    *,
    now: datetime | None = None,
    rng: random.Random | random.SystemRandom | None = None,
) -> CrateResult:
    """Abre uma crate: Básico sempre e, com sorte, 1 item de outra raridade.

    A caixa vem inteira em itens ou inteira em Memilets: Memilets nunca vêm junto com
    item. Cosméticos são sempre novos para a pessoa. Em 5% das caixas com item, o item
    vem em dobro (outro da mesma raridade).
    """
    user_id = str(discord_user_id)
    generator = rng or random.SystemRandom()
    current_time = _utc_now(now)
    register_user(user_id, display_name, database_path)
    with connect(database_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        perks = load_perks(connection, user_id)
        price = perks.crate_price
        balance = connection.execute(
            "SELECT memilets FROM game_users WHERE discord_user_id = ?",
            (user_id,),
        ).fetchone()["memilets"]
        if balance < price:
            raise InsufficientFunds(f"Uma crate custa {price} Memilets.")
        connection.execute(
            """
            UPDATE game_users SET memilets = memilets - ?, updated_at = CURRENT_TIMESTAMP
            WHERE discord_user_id = ?
            """,
            (price, user_id),
        )

        extra = None
        draw = generator.random()
        cumulative = 0.0
        for rarity, chance in CRATE_EXTRA_CHANCES.items():
            cumulative += chance
            if draw < cumulative:
                extra = rarity
                break
        pity_row = connection.execute(
            "SELECT crate_pity FROM user_upgrades WHERE discord_user_id = ? AND upgrade_key = 'rolls'",
            (user_id,),
        ).fetchone()
        pity = int(pity_row["crate_pity"]) if pity_row else 0
        guaranteed = pity + 1 >= CRATE_PITY_LIMIT and extra in (None, "incomum")
        if guaranteed:
            extra = "raro"
        pity = 0 if extra in ("raro", "epico", "lendario") else min(CRATE_PITY_LIMIT, pity + 1)
        connection.execute(
            """
            INSERT INTO user_upgrades (discord_user_id, upgrade_key, crate_pity) VALUES (?, 'rolls', ?)
            ON CONFLICT(discord_user_id, upgrade_key) DO UPDATE SET crate_pity = excluded.crate_pity
            """,
            (user_id, pity),
        )
        record(connection, user_id, "crate", now=current_time)

        prizes: list[CratePrize] = []
        doubled = False
        if generator.random() < CRATE_ITEM_CHANCE:
            items: list[CratePrize] = []
            if extra:
                first = _give_item(connection, user_id, extra, generator, database_path)
                if first:
                    items.append(first)
                    if generator.random() < CRATE_DOUBLE_CHANCE:
                        second = _give_item(connection, user_id, extra, generator, database_path)
                        if second:
                            items.append(second)
                            doubled = True
            if not extra or items:
                rolls = generator.randint(*CRATE_INSTANT_ROLLS)
                if not items and generator.random() < CRATE_DOUBLE_CHANCE:
                    rolls *= 2
                    doubled = True
                _give_instant_rolls(connection, user_id, rolls)
                prizes = [CratePrize("basico", "instant_rolls", rolls), *items]
        if not prizes:
            # Sem item disponível (ou caixa de Memilets): tudo vira Memilets.
            doubled = False
            prizes = [
                CratePrize("basico", "memilets", generator.randint(*CRATE_MEMILETS["basico"]))
            ]
            if extra:
                prizes.append(
                    CratePrize(extra, "memilets", generator.randint(*CRATE_MEMILETS[extra]))
                )
            total = sum(prize.amount for prize in prizes)
            connection.execute(
                """
                UPDATE game_users SET memilets = memilets + ?, updated_at = CURRENT_TIMESTAMP
                WHERE discord_user_id = ?
                """,
                (total, user_id),
            )
        return CrateResult(price, tuple(prizes), doubled, pity, guaranteed)


def _give_instant_rolls(connection, user_id: str, amount: int) -> None:
    """Rolls da crate vão para o estoque de rolls extras: não expiram, usa quando quiser."""
    connection.execute(
        """
        INSERT INTO user_upgrades (discord_user_id, upgrade_key, roll_tokens)
        VALUES (?, 'rolls', ?)
        ON CONFLICT(discord_user_id, upgrade_key)
        DO UPDATE SET roll_tokens = roll_tokens + excluded.roll_tokens
        """,
        (user_id, amount),
    )


def _give_item(
    connection, user_id: str, rarity: str, rng, database_path: str | Path
) -> CratePrize | None:
    """Entrega um item ainda disponível da raridade; None se nenhum serve mais."""
    perks = load_perks(connection, user_id)
    options = list(CRATE_ITEMS[rarity])
    rng.shuffle(options)
    for item in options:
        if item == "cosmetic":
            picked = random_new_item(connection, user_id, rng, database_path)
            if picked is None:
                continue
            kind, cosmetic = picked
            grant(connection, user_id, kind, cosmetic)
            return CratePrize(rarity, f"cosmetic:{kind}", 1, cosmetic["name"])
        if item == "random_upgrade":
            open_upgrades = [
                key for key, spec in UPGRADES.items() if perks.upgrades[key] < spec.limit
            ]
            if not open_upgrades:
                continue
            key = rng.choice(open_upgrades)
            _raise_upgrade(connection, user_id, key)
            return CratePrize(rarity, "random_upgrade", 1, UPGRADES[key].name)
        bonus_key, amount = CRATE_BONUS_ITEMS[item]
        if perks.bonuses[bonus_key] + amount > BONUS_LIMITS[bonus_key]:
            continue
        if bonus_key == "daily_minutes" and perks.daily_cooldown <= MIN_DAILY_COOLDOWN:
            continue
        add_bonus(connection, user_id, bonus_key, amount)
        return CratePrize(rarity, item, amount)
    return None


def describe_prize(prize: CratePrize) -> str:
    """Texto curto de um prêmio da crate, para o embed."""
    if prize.kind == "memilets":
        return f"{prize.amount:,} Memilets".replace(",", ".")
    if prize.kind == "instant_rolls":
        plural = "s" if prize.amount > 1 else ""
        return f"{prize.amount} roll{plural} guardado{plural} (use quando quiser)"
    if prize.kind.startswith("cosmetic:"):
        kind = prize.kind.split(":", 1)[1]
        label, emoji, *_ = COSMETIC_KINDS[kind]
        return f"{emoji} {label}: **{prize.name}**"
    texts = {
        "daily_1h": "-1 h no cooldown do daily (permanente)",
        "daily_2h": "-2 h no cooldown do daily (permanente)",
        "wish_protection": "+1 s de proteção de wish (permanente)",
        "star_wish_slot": "+1 slot de Star Wish (permanente)",
        "wish_slot": "+1 slot de wish (permanente)",
        "permanent_roll": "+1 roll permanente por ciclo",
        "random_upgrade": f"+1 nível em **{prize.name}**",
        "crate_discount": "-10 Memilets no preço da crate (permanente)",
    }
    return texts[prize.kind]


@dataclass(frozen=True)
class AccountStatus:
    memilets: int
    owned_players: int
    rolls_left: int
    roll_capacity: int
    roll_tokens: int
    rolls_reset_at: datetime
    claim_available_at: datetime | None
    daily_available_at: datetime | None
    wishes: int
    wish_capacity: int
    star_wishes: int
    star_wish_capacity: int
    levels: dict[str, int]
    daily_cap: int = 300
    daily_cooldown: timedelta = timedelta(hours=24)
    crate_price: int = CRATE_BASE_PRICE
    wish_protection: int = 0
    instant_rolls: int = 0


def get_account_status(
    discord_user_id: int | str,
    display_name: str,
    database_path: str | Path = DATABASE_PATH,
    *,
    now: datetime | None = None,
) -> AccountStatus:
    """Saldo, timers e limites da conta, como o `$tu` do Mudae."""
    from src.rolls import claimed_at, cycle_bonus_rolls

    current_time = _utc_now(now)
    user_id = str(discord_user_id)
    account = register_user(user_id, display_name, database_path)
    cycle_start = cycle_start_for(current_time)
    cycle_key = cycle_start.isoformat(timespec="seconds")
    with connect(database_path) as connection:
        perks = load_perks(connection, user_id)
        instant = cycle_bonus_rolls(connection, user_id, cycle_key)
        row = connection.execute(
            "SELECT last_claim_at, last_daily_at FROM game_users WHERE discord_user_id = ?",
            (user_id,),
        ).fetchone()
        usage = connection.execute(
            """
            SELECT rolls_used FROM roll_cycles
            WHERE discord_user_id = ? AND cycle_started_at = ?
            """,
            (user_id, cycle_key),
        ).fetchone()
        tokens = connection.execute(
            """
            SELECT roll_tokens FROM user_upgrades
            WHERE discord_user_id = ? AND upgrade_key = 'rolls'
            """,
            (user_id,),
        ).fetchone()
        owned = connection.execute(
            "SELECT COUNT(*) FROM player_ownership WHERE discord_user_id = ?",
            (user_id,),
        ).fetchone()[0]
        wish_counts = {
            wish["wish_type"]: int(wish["amount"])
            for wish in connection.execute(
                """
                SELECT wish_type, COUNT(*) AS amount FROM user_wishes
                WHERE discord_user_id = ? GROUP BY wish_type
                """,
                (user_id,),
            )
        }
    capacity = perks.roll_capacity + instant
    used = int(usage["rolls_used"]) if usage else 0
    claim_at = None
    if row["last_claim_at"] and claimed_at(row["last_claim_at"]) >= cycle_start:
        claim_at = cycle_start + CYCLE_LENGTH
    daily_at = None
    if row["last_daily_at"]:
        daily_at = (
            datetime.fromisoformat(row["last_daily_at"]).astimezone(timezone.utc)
            + perks.daily_cooldown
        )
        if daily_at <= current_time:
            daily_at = None
    return AccountStatus(
        memilets=int(account["memilets"]),
        owned_players=int(owned),
        rolls_left=max(0, capacity - used),
        roll_capacity=capacity,
        roll_tokens=int(tokens["roll_tokens"]) if tokens else 0,
        rolls_reset_at=cycle_start + CYCLE_LENGTH,
        claim_available_at=claim_at,
        daily_available_at=daily_at,
        wishes=wish_counts.get("normal", 0),
        wish_capacity=perks.wish_capacity,
        star_wishes=wish_counts.get("star", 0),
        star_wish_capacity=perks.star_wish_capacity,
        levels=dict(perks.upgrades),
        daily_cap=perks.daily_cap,
        daily_cooldown=perks.daily_cooldown,
        crate_price=perks.crate_price,
        wish_protection=perks.wish_protection_seconds,
        instant_rolls=instant,
    )
