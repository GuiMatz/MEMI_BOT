"""Rolagens, duplicatas e claims atômicos do jogo."""

from __future__ import annotations

import random
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from src.db import DATABASE_PATH, connect
from src.perks import (
    BASE_EXCLUSIVE_SECONDS,
    ROLLS_PER_CYCLE,
    load_perks,
)
from src.ratings import GAME_OVERALL_SQL, POOL_SQL, RARITY_TIERS, effective_overall

CYCLE_LENGTH = timedelta(minutes=30)
# Os ciclos começam na hora cheia e na meia hora (em Brasília também, já que o fuso é UTC-3).
CYCLE_OFFSET_SECONDS = 0
CLAIM_EXCLUSIVE_TIME = timedelta(seconds=BASE_EXCLUSIVE_SECONDS)
CLAIM_OFFER_LIFETIME = timedelta(minutes=5)
MAX_DUPLICATE_BALLS = 10

RARITY_BANDS = tuple((lower, upper, weight) for _, _, lower, upper, weight in RARITY_TIERS)


class RollError(Exception):
    """Erro de regra da roleta que pode ser exibido ao jogador."""


class NoEligiblePlayers(RollError):
    """O banco não possui jogadores elegíveis para a roleta."""


class RollLimitReached(RollError):
    """A conta já usou os rolls do ciclo atual."""

    def __init__(self, reset_at: datetime, capacity: int = ROLLS_PER_CYCLE):
        self.reset_at = reset_at
        self.capacity = capacity
        super().__init__("Os rolls deste ciclo acabaram.")


@dataclass(frozen=True)
class RollOutcome:
    player: dict[str, Any]
    kind: str
    rolls_remaining: int
    cycle_resets_at: datetime
    offer_id: int | None = None
    reward_amount: int | None = None
    balls: int | None = None
    effective_overall: int | None = None
    exclusive_seconds: int = BASE_EXCLUSIVE_SECONDS
    wished_by: tuple[str, ...] = ()
    protection_seconds: int = 0


@dataclass(frozen=True)
class ClaimOutcome:
    status: str
    reward_amount: int | None = None
    retry_at: datetime | None = None
    protected_until: datetime | None = None


def _utc_now(value: datetime | None) -> datetime:
    now = value or datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now deve incluir um fuso horário.")
    return now.astimezone(timezone.utc)


def _cycle_start(now: datetime) -> datetime:
    epoch_seconds = int(now.timestamp())
    cycle_index = (epoch_seconds - CYCLE_OFFSET_SECONDS) // int(CYCLE_LENGTH.total_seconds())
    return datetime.fromtimestamp(
        cycle_index * int(CYCLE_LENGTH.total_seconds()) + CYCLE_OFFSET_SECONDS,
        tz=timezone.utc,
    )


cycle_start_for = _cycle_start


def _create_offer(
    connection: sqlite3.Connection,
    player_id: int,
    roller_id: str,
    offer_type: str,
    reward_amount: int | None,
    now: datetime,
) -> int:
    cursor = connection.execute(
        """
        INSERT INTO claim_offers (
            player_id, roller_id, offer_type, reward_amount, created_at, expires_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            player_id,
            roller_id,
            offer_type,
            reward_amount,
            now.isoformat(timespec="seconds"),
            (now + CLAIM_OFFER_LIFETIME).isoformat(timespec="seconds"),
        ),
    )
    return int(cursor.lastrowid)


def roll_player(
    discord_user_id: int | str,
    display_name: str,
    database_path: str | Path = DATABASE_PATH,
    *,
    now: datetime | None = None,
    rng: random.Random | random.SystemRandom | None = None,
) -> RollOutcome:
    """Consome um roll do ciclo e sorteia um jogador ativo elegível."""
    current_time = _utc_now(now)
    generator = rng or random.SystemRandom()
    user_id = str(discord_user_id)
    cycle_start = _cycle_start(current_time)
    cycle_end = cycle_start + CYCLE_LENGTH
    cycle_key = cycle_start.isoformat(timespec="seconds")
    reset_at = cycle_end

    with connect(database_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            INSERT INTO game_users (discord_user_id, display_name)
            VALUES (?, ?)
            ON CONFLICT(discord_user_id) DO UPDATE SET
                display_name = excluded.display_name,
                updated_at = CURRENT_TIMESTAMP
            """,
            (user_id, display_name),
        )
        usage = connection.execute(
            """
            SELECT rolls_used
            FROM roll_cycles
            WHERE discord_user_id = ? AND cycle_started_at = ?
            """,
            (user_id, cycle_key),
        ).fetchone()
        rolls_used = int(usage["rolls_used"]) if usage else 0
        roll_upgrade = connection.execute(
            """
            SELECT roll_tokens FROM user_upgrades
            WHERE discord_user_id = ? AND upgrade_key = 'rolls'
            """,
            (user_id,),
        ).fetchone()
        roll_tokens = int(roll_upgrade["roll_tokens"]) if roll_upgrade else 0
        perks = load_perks(connection, user_id)
        roll_capacity = perks.roll_capacity + cycle_bonus_rolls(connection, user_id, cycle_key)
        exclusive_seconds = perks.exclusive_seconds
        use_roll_token = rolls_used >= roll_capacity
        if use_roll_token and roll_tokens == 0:
            raise RollLimitReached(reset_at, roll_capacity)

        counts = connection.execute(f"""
            SELECT rarity_index({GAME_OVERALL_SQL}) AS band, COUNT(*) AS player_count
            FROM players AS p
            WHERE {POOL_SQL}
            GROUP BY band
            """).fetchall()
        available_counts = {int(row["band"]): int(row["player_count"]) for row in counts}
        available_bands = sorted(available_counts)
        if not available_bands:
            raise NoEligiblePlayers("Não há jogadores elegíveis no pool.")

        selected_band = generator.choices(
            available_bands,
            weights=[RARITY_BANDS[index][2] for index in available_bands],
            k=1,
        )[0]
        lower_bound, upper_bound, _ = RARITY_BANDS[selected_band]
        star_wish_rows = connection.execute(
            f"""
            SELECT p.id
            FROM players AS p
            JOIN user_wishes AS w
              ON w.player_id = p.id
             AND w.discord_user_id = ?
             AND w.wish_type = 'star'
            WHERE {POOL_SQL}
              AND {GAME_OVERALL_SQL} BETWEEN ? AND ?
            """,
            (user_id, lower_bound, upper_bound),
        ).fetchall()
        star_wish_ids = [int(row["id"]) for row in star_wish_rows]
        candidate_count = available_counts[selected_band]
        if star_wish_ids and generator.random() < (1.25 * len(star_wish_ids)) / (
            candidate_count + 0.25 * len(star_wish_ids)
        ):
            selected_player_id = generator.choice(star_wish_ids)
            player_row = connection.execute(
                f"""
                SELECT p.id, p.name, p.display_name, p.club_name, p.nationality,
                       p.game_position, {GAME_OVERALL_SQL} AS overall, p.overall_source,
                       p.overall_edition, p.photo_path,
                       o.discord_user_id AS owner_id,
                       owner.display_name AS owner_name,
                       COALESCE(b.balls, 0) AS balls
                FROM players AS p
                LEFT JOIN player_ownership AS o ON o.player_id = p.id
                LEFT JOIN game_users AS owner ON owner.discord_user_id = o.discord_user_id
                LEFT JOIN player_balls AS b ON b.player_id = p.id
                WHERE p.id = ?
                """,
                (selected_player_id,),
            ).fetchone()
        else:
            star_exclusion = (
                """
              AND NOT EXISTS (
                  SELECT 1 FROM user_wishes AS w
                  WHERE w.discord_user_id = ?
                    AND w.player_id = p.id
                    AND w.wish_type = 'star'
              )
            """
                if star_wish_ids
                else ""
            )
            offset = generator.randrange(candidate_count - len(star_wish_ids))
            params: tuple[Any, ...] = (lower_bound, upper_bound)
            if star_wish_ids:
                params += (user_id,)
            params += (offset,)
            player_row = connection.execute(
                f"""
            SELECT p.id, p.name, p.display_name, p.club_name, p.nationality,
                   p.game_position, {GAME_OVERALL_SQL} AS overall, p.overall_source,
                   p.overall_edition, p.photo_path,
                   o.discord_user_id AS owner_id,
                   owner.display_name AS owner_name,
                   COALESCE(b.balls, 0) AS balls
            FROM players AS p
            LEFT JOIN player_ownership AS o ON o.player_id = p.id
            LEFT JOIN game_users AS owner ON owner.discord_user_id = o.discord_user_id
            LEFT JOIN player_balls AS b ON b.player_id = p.id
            WHERE {POOL_SQL}
              AND {GAME_OVERALL_SQL} BETWEEN ? AND ?
              {star_exclusion}
            ORDER BY p.id
            LIMIT 1 OFFSET ?
            """,
                params,
            ).fetchone()
        if player_row is None:
            raise NoEligiblePlayers("Não há jogadores elegíveis nessa faixa.")
        player = dict(player_row)

        connection.execute(
            """
            INSERT INTO roll_cycles (discord_user_id, cycle_started_at, rolls_used)
            VALUES (?, ?, 1)
            ON CONFLICT(discord_user_id, cycle_started_at)
            DO UPDATE SET rolls_used = rolls_used + 1
            """,
            (user_id, cycle_key),
        )

        if use_roll_token:
            connection.execute(
                """
                UPDATE user_upgrades SET roll_tokens = roll_tokens - 1
                WHERE discord_user_id = ? AND upgrade_key = 'rolls'
                """,
                (user_id,),
            )
            roll_tokens -= 1
        rolls_remaining = max(0, roll_capacity - rolls_used - 1) + roll_tokens
        offer_id = None
        reward_amount = None
        balls = None
        effective = effective_overall(player["overall"], player["balls"])
        wished_by: tuple[str, ...] = ()
        protection_seconds = 0
        if player["owner_id"] == user_id:
            balls = int(player["balls"])
            if balls < MAX_DUPLICATE_BALLS:
                # Andar 2 da torre: chance de a bola vir em dobro.
                gained = 2 if generator.random() < perks.double_ball_chance else 1
                gained = min(gained, MAX_DUPLICATE_BALLS - balls)
                balls += gained
                reward_amount = 100
                connection.execute(
                    """
                    INSERT INTO player_balls (player_id, balls)
                    VALUES (?, ?)
                    ON CONFLICT(player_id) DO UPDATE SET
                        balls = player_balls.balls + excluded.balls,
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    (player["id"], gained),
                )
                effective = effective_overall(player["overall"], balls)
                kind = "own_duplicate_ball" if gained == 1 else "own_duplicate_double_ball"
            else:
                reward_amount = 500
                effective = effective_overall(player["overall"], balls)
                kind = "own_duplicate_max_balls"
            connection.execute(
                """
                UPDATE game_users
                SET memilets = memilets + ?, updated_at = CURRENT_TIMESTAMP
                WHERE discord_user_id = ?
                """,
                (reward_amount, user_id),
            )
        elif player["owner_id"] is not None:
            reward_amount = generator.randint(100, 300)
            offer_id = _create_offer(
                connection,
                int(player["id"]),
                user_id,
                "duplicate_reward",
                reward_amount,
                current_time,
            )
            kind = "other_duplicate"
        else:
            offer_id = _create_offer(
                connection,
                int(player["id"]),
                user_id,
                "player_claim",
                None,
                current_time,
            )
            kind = "unowned"
            wished_by, protection_seconds = _wish_protection(connection, int(player["id"]), user_id)

        return RollOutcome(
            player=player,
            kind=kind,
            rolls_remaining=rolls_remaining,
            cycle_resets_at=reset_at,
            offer_id=offer_id,
            reward_amount=reward_amount,
            balls=balls,
            effective_overall=effective,
            exclusive_seconds=exclusive_seconds,
            wished_by=wished_by,
            protection_seconds=protection_seconds,
        )


def claimed_at(value: str) -> datetime:
    """Horário salvo do último claim, sempre em UTC (registros antigos vêm sem fuso)."""
    moment = datetime.fromisoformat(value)
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def cycle_bonus_rolls(connection: sqlite3.Connection, user_id: str, cycle_key: str) -> int:
    """Rolls instantâneos das crates: valem só no ciclo em que foram ganhos."""
    row = connection.execute(
        """
        SELECT amount FROM cycle_bonus_rolls
        WHERE discord_user_id = ? AND cycle_started_at = ?
        """,
        (user_id, cycle_key),
    ).fetchone()
    return int(row["amount"]) if row else 0


def _wish_protection(
    connection: sqlite3.Connection, player_id: int, roller_id: str
) -> tuple[tuple[str, ...], int]:
    """Quem tem wish no jogador (fora quem rolou) e a maior proteção entre eles."""
    wishers = tuple(
        str(row["discord_user_id"])
        for row in connection.execute(
            """
            SELECT discord_user_id FROM user_wishes
            WHERE player_id = ? AND discord_user_id != ?
            ORDER BY wish_type DESC, created_at
            """,
            (player_id, roller_id),
        )
    )
    protection = max(
        (load_perks(connection, wisher).wish_protection_seconds for wisher in wishers),
        default=0,
    )
    return wishers, protection


def claim_roll_offer(
    offer_id: int,
    discord_user_id: int | str,
    database_path: str | Path = DATABASE_PATH,
    *,
    now: datetime | None = None,
) -> ClaimOutcome:
    """Resgata a moeda ou atribui o jogador em uma transação atômica."""
    current_time = _utc_now(now)
    user_id = str(discord_user_id)
    with connect(database_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        offer = connection.execute(
            """
            SELECT id, player_id, roller_id, offer_type, reward_amount, status,
                   created_at, expires_at
            FROM claim_offers
            WHERE id = ?
            """,
            (offer_id,),
        ).fetchone()
        if offer is None:
            return ClaimOutcome("not_found")
        if offer["status"] != "pending":
            return ClaimOutcome("already_claimed")

        expires_at = datetime.fromisoformat(offer["expires_at"])
        if current_time >= expires_at:
            connection.execute(
                "UPDATE claim_offers SET status = 'expired' WHERE id = ?",
                (offer_id,),
            )
            return ClaimOutcome("expired")

        if offer["offer_type"] == "duplicate_reward":
            if user_id != offer["roller_id"]:
                return ClaimOutcome("not_roller")
            reward_amount = int(offer["reward_amount"])
            connection.execute(
                """
                UPDATE game_users
                SET memilets = memilets + ?, updated_at = CURRENT_TIMESTAMP
                WHERE discord_user_id = ?
                """,
                (reward_amount, user_id),
            )
            connection.execute(
                """
                UPDATE claim_offers
                SET status = 'claimed', claimed_by = ?, claimed_at = ?
                WHERE id = ? AND status = 'pending'
                """,
                (user_id, current_time.isoformat(timespec="seconds"), offer_id),
            )
            return ClaimOutcome("reward_claimed", reward_amount=reward_amount)

        created_at = datetime.fromisoformat(offer["created_at"])
        has_wish = connection.execute(
            """
            SELECT 1 FROM user_wishes
            WHERE discord_user_id = ? AND player_id = ?
            """,
            (user_id, offer["player_id"]),
        ).fetchone()
        # Proteção de wish: por alguns segundos só quem tem wish no jogador pode contratar.
        # Vários donos de wish podem pegar; um ignora a proteção do outro.
        _, protection = _wish_protection(connection, int(offer["player_id"]), offer["roller_id"])
        protected_until = created_at + timedelta(seconds=protection)
        if protection and not has_wish and current_time < protected_until:
            return ClaimOutcome("wish_protected", protected_until=protected_until)
        if user_id != offer["roller_id"]:
            exclusive_seconds = load_perks(connection, offer["roller_id"]).exclusive_seconds
            exclusivity_end = created_at + timedelta(seconds=exclusive_seconds)
            if not has_wish and current_time < exclusivity_end:
                return ClaimOutcome("exclusive")

        account = connection.execute(
            "SELECT last_claim_at FROM game_users WHERE discord_user_id = ?",
            (user_id,),
        ).fetchone()
        if account is None:
            raise LookupError("A conta de jogo ainda não foi cadastrada.")
        claimer_perks = load_perks(connection, user_id)
        # Um claim por ciclo: o claim renova junto com os rolls (hora cheia e meia hora).
        cycle_start = _cycle_start(current_time)
        if account["last_claim_at"] and claimed_at(account["last_claim_at"]) >= cycle_start:
            return ClaimOutcome("cooldown", retry_at=cycle_start + CYCLE_LENGTH)

        owner = connection.execute(
            "SELECT discord_user_id FROM player_ownership WHERE player_id = ?",
            (offer["player_id"],),
        ).fetchone()
        if owner is not None:
            connection.execute(
                "UPDATE claim_offers SET status = 'unavailable' WHERE id = ?",
                (offer_id,),
            )
            return ClaimOutcome("unavailable")

        connection.execute(
            "INSERT INTO player_ownership (player_id, discord_user_id) VALUES (?, ?)",
            (offer["player_id"], user_id),
        )
        connection.execute(
            """
            UPDATE game_users SET last_claim_at = ?, updated_at = CURRENT_TIMESTAMP
            WHERE discord_user_id = ?
            """,
            (current_time.isoformat(timespec="seconds"), user_id),
        )
        connection.execute(
            """
            UPDATE claim_offers
            SET status = 'claimed', claimed_by = ?, claimed_at = ?
            WHERE id = ? AND status = 'pending'
            """,
            (user_id, current_time.isoformat(timespec="seconds"), offer_id),
        )
        # Andar 3 da torre: Memilets iguais ao overall de quem foi contratado.
        reward = None
        if claimer_perks.floor(3):
            overall = connection.execute(
                f"SELECT {GAME_OVERALL_SQL} AS overall FROM players AS p WHERE p.id = ?",
                (offer["player_id"],),
            ).fetchone()["overall"]
            reward = int(overall or 0) * claimer_perks.floor(3)
            if reward:
                connection.execute(
                    "UPDATE game_users SET memilets = memilets + ? WHERE discord_user_id = ?",
                    (reward, user_id),
                )
        return ClaimOutcome("player_claimed", reward_amount=reward or None)
