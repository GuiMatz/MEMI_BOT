"""Recompensas, upgrades, crates e wishes."""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from src.db import DATABASE_PATH, connect
from src.game import register_user
from src.rolls import CLAIM_COOLDOWN, CYCLE_LENGTH, ROLLS_PER_CYCLE, cycle_start_for

CRATE_PRICE = 500
CRATE_PITY_LIMIT = 50
# Chance de cada prêmio. Memilets sempre devolvem pelo menos 70% do preço e
# o valor esperado de uma crate passa do que ela custa.
CRATE_WEIGHTS = {
    "memilets": 50.0,
    "roll_tokens": 28.0,
    "claim_reset": 10.0,
    "jackpot": 4.0,
    "wishes": 3.0,
    "rolls": 2.5,
    "star_wishes": 2.5,
}
DAILY_TIMEZONE = ZoneInfo("America/Sao_Paulo")

UPGRADE_LIMITS = {
    "rolls": 90,
    "wishes": 20,
    "daily": 17,
    "exclusivity": 10,
    "star_wishes": 4,
    "duplicate_bonus": 5,
}

UPGRADE_LABELS = {
    "rolls": "rolls por ciclo",
    "wishes": "slots de wish",
    "daily": "níveis de daily",
    "exclusivity": "segundos de exclusividade",
    "star_wishes": "slots de Star Wish",
    "duplicate_bonus": "bônus de duplicata",
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
class CrateReward:
    kind: str
    amount: int
    pity: int


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
        rows = connection.execute(
            """
            SELECT upgrade_key, level
            FROM user_upgrades
            WHERE discord_user_id = ?
            """,
            (str(discord_user_id),),
        ).fetchall()
    levels = {key: 0 for key in UPGRADE_LIMITS}
    levels.update({row["upgrade_key"]: int(row["level"]) for row in rows})
    return levels


def get_upgrade_cost(key: str, current_level: int) -> int:
    if key not in UPGRADE_LIMITS:
        raise ValueError(f"Upgrade desconhecido: {key}")
    if current_level >= UPGRADE_LIMITS[key]:
        raise UpgradeMaxed(f"{UPGRADE_LABELS[key]} já atingiu o limite.")
    if key == "rolls":
        return 5_000 * (current_level + 1)
    if key == "wishes":
        return 3_000 * (current_level + 1)
    if key == "daily":
        return 5_000
    if key == "exclusivity":
        return 5_000 * (current_level + 1)
    if key == "duplicate_bonus":
        return 4_000 * (current_level + 1)
    if key == "star_wishes":
        raise UpgradeMaxed("Slots de Star Wish podem ser ganhos em crates.")
    raise ValueError(f"Upgrade desconhecido: {key}")


def buy_upgrade(
    discord_user_id: int | str,
    display_name: str,
    key: str,
    database_path: str | Path = DATABASE_PATH,
) -> UpgradePurchase:
    if key not in UPGRADE_LIMITS:
        raise ValueError("Upgrade inválido.")
    if key == "star_wishes":
        raise UpgradeMaxed("Slots de Star Wish podem ser ganhos em crates.")
    user_id = str(discord_user_id)
    register_user(user_id, display_name, database_path)
    with connect(database_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            """
            SELECT level FROM user_upgrades
            WHERE discord_user_id = ? AND upgrade_key = ?
            """,
            (user_id, key),
        ).fetchone()
        level = int(row["level"]) if row else 0
        cost = get_upgrade_cost(key, level)
        balance = connection.execute(
            "SELECT memilets FROM game_users WHERE discord_user_id = ?",
            (user_id,),
        ).fetchone()["memilets"]
        if balance < cost:
            raise InsufficientFunds(
                f"Você precisa de {cost} Memilets para comprar este upgrade."
            )
        connection.execute(
            """
            UPDATE game_users
            SET memilets = memilets - ?, updated_at = CURRENT_TIMESTAMP
            WHERE discord_user_id = ?
            """,
            (cost, user_id),
        )
        connection.execute(
            """
            INSERT INTO user_upgrades (discord_user_id, upgrade_key, level)
            VALUES (?, ?, 1)
            ON CONFLICT(discord_user_id, upgrade_key)
            DO UPDATE SET level = user_upgrades.level + 1
            """,
            (user_id, key),
        )
        return UpgradePurchase(key, level + 1, cost)


def claim_daily(
    discord_user_id: int | str,
    display_name: str,
    database_path: str | Path = DATABASE_PATH,
    *,
    now: datetime | None = None,
    rng: random.Random | random.SystemRandom | None = None,
) -> DailyReward:
    current_time = _utc_now(now)
    user_id = str(discord_user_id)
    generator = rng or random.SystemRandom()
    register_user(user_id, display_name, database_path)
    with connect(database_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        account = connection.execute(
            "SELECT last_daily_at FROM game_users WHERE discord_user_id = ?",
            (user_id,),
        ).fetchone()
        if account["last_daily_at"]:
            available_at = datetime.fromisoformat(
                account["last_daily_at"]
            ).astimezone(timezone.utc) + timedelta(hours=24)
            if current_time < available_at:
                raise DailyCooldown(available_at)
        row = connection.execute(
            """
            SELECT level FROM user_upgrades
            WHERE discord_user_id = ? AND upgrade_key = 'daily'
            """,
            (user_id,),
        ).fetchone()
        cap = min(2_000, 300 + (int(row["level"]) if row else 0) * 100)
        amount = generator.randint(200, cap)
        connection.execute(
            """
            UPDATE game_users
            SET memilets = memilets + ?, last_daily_at = ?, updated_at = CURRENT_TIMESTAMP
            WHERE discord_user_id = ?
            """,
            (amount, current_time.isoformat(timespec="seconds"), user_id),
        )
        available_at = current_time + timedelta(hours=24)
        return DailyReward(amount, available_at, cap)


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
        current = connection.execute(
            """
            SELECT wish_type FROM user_wishes
            WHERE discord_user_id = ? AND player_id = ?
            """,
            (user_id, player_id),
        ).fetchone()
        if current:
            if current["wish_type"] == wish_type:
                connection.execute(
                    "DELETE FROM user_wishes WHERE discord_user_id = ? AND player_id = ?",
                    (user_id, player_id),
                )
                return "removed"
            counts = connection.execute(
                """
                SELECT COUNT(*) AS amount FROM user_wishes
                WHERE discord_user_id = ? AND wish_type = ?
                """,
                (user_id, wish_type),
            ).fetchone()
            upgrade = connection.execute(
                """
                SELECT level FROM user_upgrades
                WHERE discord_user_id = ? AND upgrade_key = ?
                """,
                (user_id, "wishes" if wish_type == "normal" else "star_wishes"),
            ).fetchone()
            capacity = (5 if wish_type == "normal" else 1) + (
                int(upgrade["level"]) if upgrade else 0
            )
            if int(counts["amount"]) >= capacity:
                raise EconomyError(
                    f"Você já está usando todos os {capacity} slots de "
                    f"{'wish' if wish_type == 'normal' else 'Star Wish'}."
                )
            connection.execute(
                """
                UPDATE user_wishes SET wish_type = ?, created_at = CURRENT_TIMESTAMP
                WHERE discord_user_id = ? AND player_id = ?
                """,
                (wish_type, user_id, player_id),
            )
            return "updated"
        counts = connection.execute(
            """
            SELECT wish_type, COUNT(*) AS amount FROM user_wishes
            WHERE discord_user_id = ? GROUP BY wish_type
            """,
            (user_id,),
        ).fetchall()
        count_by_type = {row["wish_type"]: int(row["amount"]) for row in counts}
        upgrade = connection.execute(
            """
            SELECT level FROM user_upgrades
            WHERE discord_user_id = ? AND upgrade_key = ?
            """,
            (user_id, "wishes" if wish_type == "normal" else "star_wishes"),
        ).fetchone()
        capacity = (5 if wish_type == "normal" else 1) + (
            int(upgrade["level"]) if upgrade else 0
        )
        if count_by_type.get(wish_type, 0) >= capacity:
            raise EconomyError(
                f"Você já está usando todos os {capacity} slots de "
                f"{'wish' if wish_type == 'normal' else 'Star Wish'}."
            )
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


def open_crate(
    discord_user_id: int | str,
    display_name: str,
    database_path: str | Path = DATABASE_PATH,
    *,
    rng: random.Random | random.SystemRandom | None = None,
) -> CrateReward:
    user_id = str(discord_user_id)
    generator = rng or random.SystemRandom()
    register_user(user_id, display_name, database_path)
    with connect(database_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        account = connection.execute(
            "SELECT memilets FROM game_users WHERE discord_user_id = ?",
            (user_id,),
        ).fetchone()
        if account["memilets"] < CRATE_PRICE:
            raise InsufficientFunds("Uma crate custa 500 Memilets.")
        connection.execute(
            """
            UPDATE game_users SET memilets = memilets - ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE discord_user_id = ?
            """,
            (CRATE_PRICE, user_id),
        )
        pity_row = connection.execute(
            """
            SELECT crate_pity FROM user_upgrades
            WHERE discord_user_id = ? AND upgrade_key = 'rolls'
            """,
            (user_id,),
        ).fetchone()
        pity = int(pity_row["crate_pity"]) if pity_row else 0
        permanent_available = []
        for key in ("star_wishes", "wishes", "rolls"):
            level_row = connection.execute(
                """
                SELECT level FROM user_upgrades
                WHERE discord_user_id = ? AND upgrade_key = ?
                """,
                (user_id, key),
            ).fetchone()
            level = int(level_row["level"]) if level_row else 0
            if level < UPGRADE_LIMITS[key]:
                permanent_available.append(key)

        pity_triggered = pity >= CRATE_PITY_LIMIT and bool(permanent_available)
        if pity_triggered:
            pity_weights = {
                "star_wishes": 50,
                "wishes": 30,
                "rolls": 20,
            }
            reward_key = generator.choices(
                permanent_available,
                weights=[pity_weights[key] for key in permanent_available],
                k=1,
            )[0]
        else:
            reward_key = generator.choices(
                tuple(CRATE_WEIGHTS),
                weights=tuple(CRATE_WEIGHTS.values()),
                k=1,
            )[0]
            if reward_key in {"star_wishes", "wishes", "rolls"} and reward_key not in permanent_available:
                reward_key = "jackpot"

        if reward_key == "claim_reset":
            account = connection.execute(
                "SELECT last_claim_at FROM game_users WHERE discord_user_id = ?",
                (user_id,),
            ).fetchone()
            on_cooldown = bool(account["last_claim_at"]) and (
                datetime.fromisoformat(account["last_claim_at"]) + CLAIM_COOLDOWN
                > _utc_now(None)
            )
            if on_cooldown:
                connection.execute(
                    "UPDATE game_users SET last_claim_at = NULL WHERE discord_user_id = ?",
                    (user_id,),
                )
                kind = "claim_reset"
                amount = 1
            else:
                # Sem claim em espera, o reset viraria nada: entrega rolls extras.
                reward_key = "roll_tokens"

        if reward_key in {"memilets", "jackpot"}:
            low, high = (350, 900) if reward_key == "memilets" else (2_000, 5_000)
            amount = generator.randint(low, high)
            connection.execute(
                """
                UPDATE game_users SET memilets = memilets + ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE discord_user_id = ?
                """,
                (amount, user_id),
            )
            kind = reward_key
        elif reward_key == "roll_tokens":
            amount = generator.randint(3, 8)
            connection.execute(
                """
                INSERT INTO user_upgrades
                    (discord_user_id, upgrade_key, roll_tokens)
                VALUES (?, 'rolls', ?)
                ON CONFLICT(discord_user_id, upgrade_key)
                DO UPDATE SET roll_tokens = user_upgrades.roll_tokens + excluded.roll_tokens
                """,
                (user_id, amount),
            )
            kind = "roll_tokens"
        elif reward_key != "claim_reset":
            reward_key = str(reward_key)
            connection.execute(
                """
                INSERT INTO user_upgrades (discord_user_id, upgrade_key, level)
                VALUES (?, ?, 1)
                ON CONFLICT(discord_user_id, upgrade_key)
                DO UPDATE SET level = user_upgrades.level + 1
                """,
                (user_id, reward_key),
            )
            kind = reward_key
            amount = 1

        if kind in {"star_wishes", "wishes", "rolls"}:
            new_pity = 0
        elif pity_triggered:
            new_pity = 0
        else:
            new_pity = min(CRATE_PITY_LIMIT, pity + 1)
        connection.execute(
            """
            INSERT INTO user_upgrades
                (discord_user_id, upgrade_key, crate_pity)
            VALUES (?, 'rolls', ?)
            ON CONFLICT(discord_user_id, upgrade_key)
            DO UPDATE SET crate_pity = excluded.crate_pity
            """,
            (user_id, new_pity),
        )
        return CrateReward(kind, amount, new_pity)


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
    crate_pity: int
    levels: dict[str, int]


def get_account_status(
    discord_user_id: int | str,
    display_name: str,
    database_path: str | Path = DATABASE_PATH,
    *,
    now: datetime | None = None,
) -> AccountStatus:
    """Saldo, timers e limites da conta, como o `$tu` do Mudae."""
    current_time = _utc_now(now)
    user_id = str(discord_user_id)
    account = register_user(user_id, display_name, database_path)
    levels = get_upgrade_levels(user_id, database_path)
    cycle_start = cycle_start_for(current_time)
    with connect(database_path) as connection:
        row = connection.execute(
            "SELECT last_claim_at, last_daily_at FROM game_users WHERE discord_user_id = ?",
            (user_id,),
        ).fetchone()
        usage = connection.execute(
            """
            SELECT rolls_used FROM roll_cycles
            WHERE discord_user_id = ? AND cycle_started_at = ?
            """,
            (user_id, cycle_start.isoformat(timespec="seconds")),
        ).fetchone()
        tokens = connection.execute(
            """
            SELECT roll_tokens, crate_pity FROM user_upgrades
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
    capacity = ROLLS_PER_CYCLE + levels["rolls"]
    used = int(usage["rolls_used"]) if usage else 0
    claim_at = None
    if row["last_claim_at"]:
        claim_at = datetime.fromisoformat(row["last_claim_at"]) + CLAIM_COOLDOWN
        if claim_at <= current_time:
            claim_at = None
    daily_at = None
    if row["last_daily_at"]:
        daily_at = datetime.fromisoformat(row["last_daily_at"]).astimezone(
            timezone.utc
        ) + timedelta(hours=24)
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
        wish_capacity=5 + levels["wishes"],
        star_wishes=wish_counts.get("star", 0),
        star_wish_capacity=1 + levels["star_wishes"],
        crate_pity=int(tokens["crate_pity"]) if tokens else 0,
        levels=levels,
    )
