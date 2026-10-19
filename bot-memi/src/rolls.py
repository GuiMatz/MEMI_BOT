"""Rolagens, duplicatas e claims atômicos do jogo."""

from __future__ import annotations

import random
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from src.db import DATABASE_PATH, connect

ROLLS_PER_CYCLE = 10
CYCLE_LENGTH = timedelta(minutes=30)
CLAIM_EXCLUSIVE_TIME = timedelta(seconds=5)
CLAIM_OFFER_LIFETIME = timedelta(minutes=5)
CLAIM_COOLDOWN = timedelta(hours=1)
MAX_DUPLICATE_BALLS = 10

RARITY_BANDS = (
    (1, 59, 51.85),
    (60, 69, 30.0),
    (70, 79, 15.0),
    (80, 86, 3.0),
    (87, 89, 0.1),
    (90, 94, 0.04),
    (95, 97, 0.008),
    (98, 100, 0.002),
)


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
    exclusive_seconds: int = 5


@dataclass(frozen=True)
class ClaimOutcome:
    status: str
    reward_amount: int | None = None
    retry_at: datetime | None = None


def _utc_now(value: datetime | None) -> datetime:
    now = value or datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now deve incluir um fuso horário.")
    return now.astimezone(timezone.utc)


def _cycle_start(now: datetime) -> datetime:
    epoch_seconds = int(now.timestamp())
    cycle_index = (epoch_seconds - 10 * 60) // int(CYCLE_LENGTH.total_seconds())
    return datetime.fromtimestamp(
        cycle_index * int(CYCLE_LENGTH.total_seconds()) + 10 * 60,
        tz=timezone.utc,
    )


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
            SELECT level, roll_tokens FROM user_upgrades
            WHERE discord_user_id = ? AND upgrade_key = 'rolls'
            """,
            (user_id,),
        ).fetchone()
        roll_upgrade_level = int(roll_upgrade["level"]) if roll_upgrade else 0
        roll_tokens = int(roll_upgrade["roll_tokens"]) if roll_upgrade else 0
        roll_capacity = ROLLS_PER_CYCLE + roll_upgrade_level
        exclusivity_upgrade = connection.execute(
            """
            SELECT level FROM user_upgrades
            WHERE discord_user_id = ? AND upgrade_key = 'exclusivity'
            """,
            (user_id,),
        ).fetchone()
        exclusive_seconds = CLAIM_EXCLUSIVE_TIME.seconds + (
            int(exclusivity_upgrade["level"]) if exclusivity_upgrade else 0
        )
        use_roll_token = rolls_used >= roll_capacity
        if use_roll_token and roll_tokens == 0:
            raise RollLimitReached(reset_at, roll_capacity)

        counts = connection.execute("""
            SELECT CASE
                WHEN overall BETWEEN 1 AND 59 THEN 0
                WHEN overall BETWEEN 60 AND 69 THEN 1
                WHEN overall BETWEEN 70 AND 79 THEN 2
                WHEN overall BETWEEN 80 AND 86 THEN 3
                WHEN overall BETWEEN 87 AND 89 THEN 4
                WHEN overall BETWEEN 90 AND 94 THEN 5
                WHEN overall BETWEEN 95 AND 97 THEN 6
                WHEN overall BETWEEN 98 AND 100 THEN 7
            END AS band, COUNT(*) AS player_count
            FROM players
            WHERE activity_status = 'active'
              AND phase2_candidate = 0
              AND overall IS NOT NULL
              AND game_position IS NOT NULL
            GROUP BY band
            """).fetchall()
        available_counts = {
            int(row["band"]): int(row["player_count"]) for row in counts
        }
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
            """
            SELECT p.id
            FROM players AS p
            JOIN user_wishes AS w
              ON w.player_id = p.id
             AND w.discord_user_id = ?
             AND w.wish_type = 'star'
            WHERE p.activity_status = 'active'
              AND p.phase2_candidate = 0
              AND p.overall BETWEEN ? AND ?
              AND p.game_position IS NOT NULL
            """,
            (user_id, lower_bound, upper_bound),
        ).fetchall()
        star_wish_ids = [int(row["id"]) for row in star_wish_rows]
        candidate_count = available_counts[selected_band]
        if star_wish_ids and generator.random() < (
            1.25 * len(star_wish_ids)
        ) / (candidate_count + 0.25 * len(star_wish_ids)):
            selected_player_id = generator.choice(star_wish_ids)
            player_row = connection.execute(
                """
                SELECT p.id, p.name, p.display_name, p.club_name, p.nationality,
                       p.game_position, p.overall, p.overall_source, p.overall_edition,
                       p.photo_path,
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
            star_exclusion = """
              AND NOT EXISTS (
                  SELECT 1 FROM user_wishes AS w
                  WHERE w.discord_user_id = ?
                    AND w.player_id = p.id
                    AND w.wish_type = 'star'
              )
            """ if star_wish_ids else ""
            offset = generator.randrange(candidate_count - len(star_wish_ids))
            params: tuple[Any, ...] = (lower_bound, upper_bound)
            if star_wish_ids:
                params += (user_id,)
            params += (offset,)
            player_row = connection.execute(
                f"""
            SELECT p.id, p.name, p.display_name, p.club_name, p.nationality,
                   p.game_position, p.overall, p.overall_source, p.overall_edition,
                   p.photo_path,
                   o.discord_user_id AS owner_id,
                   owner.display_name AS owner_name,
                   COALESCE(b.balls, 0) AS balls
            FROM players AS p
            LEFT JOIN player_ownership AS o ON o.player_id = p.id
            LEFT JOIN game_users AS owner ON owner.discord_user_id = o.discord_user_id
            LEFT JOIN player_balls AS b ON b.player_id = p.id
            WHERE p.activity_status = 'active'
              AND p.phase2_candidate = 0
              AND p.overall BETWEEN ? AND ?
              AND p.game_position IS NOT NULL
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
        effective_overall = min(100, int(player["overall"]) + int(player["balls"]))
        if player["owner_id"] == user_id:
            balls = int(player["balls"])
            if balls < MAX_DUPLICATE_BALLS:
                balls += 1
                reward_amount = 100
                connection.execute(
                    """
                    INSERT INTO player_balls (player_id, balls)
                    VALUES (?, 1)
                    ON CONFLICT(player_id) DO UPDATE SET
                        balls = player_balls.balls + 1,
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    (player["id"],),
                )
                effective_overall = min(100, int(player["overall"]) + balls)
                kind = "own_duplicate_ball"
            else:
                reward_amount = 500
                effective_overall = min(100, int(player["overall"]) + balls)
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
            bonus = connection.execute(
                """SELECT level FROM user_upgrades
                   WHERE discord_user_id = ? AND upgrade_key = 'duplicate_bonus'""",
                (user_id,),
            ).fetchone()
            bonus_amount = int(bonus["level"]) * 50 if bonus else 0
            reward_amount = generator.randint(100, 300) + bonus_amount
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

        return RollOutcome(
            player=player,
            kind=kind,
            rolls_remaining=rolls_remaining,
            cycle_resets_at=reset_at,
            offer_id=offer_id,
            reward_amount=reward_amount,
            balls=balls,
            effective_overall=effective_overall,
            exclusive_seconds=exclusive_seconds,
        )


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

        if (
            user_id != offer["roller_id"]
        ):
            exclusivity = connection.execute(
                """
                SELECT level FROM user_upgrades
                WHERE discord_user_id = ? AND upgrade_key = 'exclusivity'
                """,
                (offer["roller_id"],),
            ).fetchone()
            exclusive_seconds = CLAIM_EXCLUSIVE_TIME.total_seconds() + (
                int(exclusivity["level"]) if exclusivity else 0
            )
            has_wish = connection.execute(
                """
                SELECT 1 FROM user_wishes
                WHERE discord_user_id = ? AND player_id = ?
                """,
                (user_id, offer["player_id"]),
            ).fetchone()
            exclusivity_end = datetime.fromisoformat(offer["created_at"]) + timedelta(
                seconds=exclusive_seconds
            )
            if not has_wish and current_time < exclusivity_end:
                return ClaimOutcome("exclusive")

        account = connection.execute(
            "SELECT last_claim_at FROM game_users WHERE discord_user_id = ?",
            (user_id,),
        ).fetchone()
        if account is None:
            raise LookupError("A conta de jogo ainda não foi cadastrada.")
        if account["last_claim_at"]:
            retry_at = datetime.fromisoformat(account["last_claim_at"]) + CLAIM_COOLDOWN
            if current_time < retry_at:
                return ClaimOutcome("cooldown", retry_at=retry_at)

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
        return ClaimOutcome("player_claimed")
