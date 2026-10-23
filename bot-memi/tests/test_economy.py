from datetime import datetime, timedelta, timezone
import random
import tempfile
import unittest
from pathlib import Path

from src.db import connect, initialize_database
from src.perks import DAILY_BASE_CAP, DAILY_MAX_STEP, DAILY_MIN_AMOUNT
from src.economy import (
    DailyCooldown,
    EconomyError,
    InsufficientFunds,
    add_wish,
    buy_upgrade,
    claim_daily,
    get_account_status,
    get_upgrade_cost,
    open_crate,
)
from src.game import get_wallet, register_user

NOW = datetime(2026, 10, 18, 16, 30, tzinfo=timezone.utc)


class EconomyTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.tempdir.name) / "game.sqlite"
        initialize_database(self.database_path)
        register_user(100, "Jogadora", self.database_path)

    def tearDown(self):
        self.tempdir.cleanup()

    def add_player(self, name):
        with connect(self.database_path) as connection:
            return connection.execute(
                """
                INSERT INTO players (
                    name, display_name, game_position, overall,
                    activity_status, phase2_candidate
                ) VALUES (?, ?, 'CA', 80, 'active', 0)
                """,
                (name, name),
            ).lastrowid

    def set_balance(self, amount):
        with connect(self.database_path) as connection:
            connection.execute(
                "UPDATE game_users SET memilets = ? WHERE discord_user_id = '100'",
                (amount,),
            )

    def test_daily_reward_is_once_after_24_hours(self):
        first = claim_daily(
            100,
            "Jogadora",
            self.database_path,
            now=NOW,
            rng=random.Random(2),
        )
        self.assertEqual((DAILY_MIN_AMOUNT, DAILY_BASE_CAP), (400, 1_200))
        self.assertGreaterEqual(first.amount, DAILY_MIN_AMOUNT)
        self.assertLessEqual(first.amount, DAILY_BASE_CAP)
        self.assertEqual(
            first.available_at,
            NOW + timedelta(hours=24),
        )
        with self.assertRaises(DailyCooldown) as error:
            claim_daily(
                100,
                "Jogadora",
                self.database_path,
                now=NOW + timedelta(hours=8),
                rng=random.Random(3),
            )
        self.assertEqual(error.exception.available_at, first.available_at)
        next_day = claim_daily(
            100,
            "Jogadora",
            self.database_path,
            now=first.available_at,
            rng=random.Random(4),
        )
        self.assertGreaterEqual(next_day.amount, DAILY_MIN_AMOUNT)

    def test_daily_upgrades_raise_cap_and_cut_cooldown(self):
        self.set_balance(100_000)
        self.assertEqual(get_upgrade_cost("rolls", 0), 2_000)
        self.assertEqual(get_upgrade_cost("rolls", 1), 3_000)
        self.assertEqual(get_upgrade_cost("wishes", 1), 1_500)
        cap = buy_upgrade(100, "Jogadora", "daily_max", self.database_path)
        faster = buy_upgrade(100, "Jogadora", "daily", self.database_path)
        self.assertEqual((cap.cost, faster.cost), (2_500, 1_000))
        reward = claim_daily(
            100,
            "Jogadora",
            self.database_path,
            now=NOW,
            rng=random.Random(5),
        )
        self.assertEqual(reward.cap, DAILY_BASE_CAP + DAILY_MAX_STEP)
        self.assertEqual(reward.available_at, NOW + timedelta(hours=24) - timedelta(minutes=15))
        self.assertEqual(get_wallet(100, self.database_path)["memilets"], 96_500 + reward.amount)

    def test_upgrade_purchase_rejects_insufficient_balance(self):
        with self.assertRaises(InsufficientFunds):
            buy_upgrade(100, "Jogadora", "rolls", self.database_path)
        self.assertEqual(get_wallet(100, self.database_path)["memilets"], 0)

    def test_wishes_have_default_slots_and_toggle_cleanly(self):
        player_ids = [self.add_player(f"Atacante {index}") for index in range(6)]
        for player_id in player_ids[:5]:
            self.assertEqual(
                add_wish(100, player_id, "normal", self.database_path),
                "added",
            )
        with self.assertRaises(EconomyError):
            add_wish(100, player_ids[5], "normal", self.database_path)
        self.assertEqual(
            add_wish(100, player_ids[5], "star", self.database_path),
            "added",
        )
        self.assertEqual(
            add_wish(100, player_ids[5], "star", self.database_path),
            "removed",
        )

    def test_crate_costs_500_and_awards_a_valid_prize(self):
        self.set_balance(500)
        result = open_crate(
            100,
            "Jogadora",
            self.database_path,
            rng=random.Random(10),
        )
        self.assertEqual(result.price, 500)
        self.assertIn(result.prizes[0].kind, {"memilets", "instant_rolls"})
        self.assertEqual(get_wallet(100, self.database_path)["memilets"], result.memilets)

    def test_crates_reject_insufficient_funds(self):
        with self.assertRaises(InsufficientFunds):
            open_crate(100, "Jogadora", self.database_path, rng=random.Random(1))

    def test_account_status_claim_resets_with_roll_cycle(self):
        register_user(100, "Jogadora", self.database_path)
        status = get_account_status(100, "Jogadora", self.database_path, now=NOW)
        self.assertIsNone(status.claim_available_at)

        # Registra claim dentro do ciclo das 16:00 (16:35)
        with connect(self.database_path) as conn:
            conn.execute(
                "UPDATE game_users SET last_claim_at = ? WHERE discord_user_id = '100'",
                ((NOW + timedelta(minutes=5)).isoformat(timespec="seconds"),),
            )
        # Consulta no mesmo ciclo (16:40): claim_available_at aponta para o próximo ciclo (17:00)
        status = get_account_status(
            100, "Jogadora", self.database_path, now=NOW + timedelta(minutes=10)
        )
        self.assertEqual(
            status.claim_available_at, datetime(2026, 10, 18, 17, 0, tzinfo=timezone.utc)
        )

        # Consulta no ciclo seguinte (17:01 em diante): claim_available_at volta a ser None
        status = get_account_status(
            100,
            "Jogadora",
            self.database_path,
            now=datetime(2026, 10, 18, 17, 1, tzinfo=timezone.utc),
        )
        self.assertIsNone(status.claim_available_at)


if __name__ == "__main__":
    unittest.main()
