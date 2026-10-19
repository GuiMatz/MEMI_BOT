from datetime import datetime, timedelta, timezone
import random
import tempfile
import unittest
from pathlib import Path

from src.db import connect, initialize_database
from src.economy import (
    DailyCooldown,
    EconomyError,
    InsufficientFunds,
    add_wish,
    buy_upgrade,
    claim_daily,
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
        self.assertGreaterEqual(first.amount, 200)
        self.assertLessEqual(first.amount, 300)
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
        self.assertGreaterEqual(next_day.amount, 200)

    def test_daily_upgrade_increases_cap_and_costs_are_progressive(self):
        self.set_balance(100_000)
        self.assertEqual(get_upgrade_cost("rolls", 0), 5_000)
        self.assertEqual(get_upgrade_cost("rolls", 1), 10_000)
        self.assertEqual(get_upgrade_cost("wishes", 1), 6_000)
        purchase = buy_upgrade(
            100,
            "Jogadora",
            "daily",
            self.database_path,
        )
        self.assertEqual(purchase.cost, 5_000)
        reward = claim_daily(
            100,
            "Jogadora",
            self.database_path,
            now=NOW,
            rng=random.Random(5),
        )
        self.assertEqual(reward.cap, 400)
        self.assertEqual(get_wallet(100, self.database_path)["memilets"], 95_000 + reward.amount)

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
        reward = open_crate(
            100,
            "Jogadora",
            self.database_path,
            rng=random.Random(10),
        )
        self.assertIn(
            reward.kind,
            {"memilets", "roll_tokens", "star_wishes", "wishes", "rolls"},
        )
        self.assertGreaterEqual(get_wallet(100, self.database_path)["memilets"], 0)
        with connect(self.database_path) as connection:
            self.assertIsNotNone(
                connection.execute(
                    "SELECT crate_pity FROM user_upgrades WHERE upgrade_key = 'rolls'"
                ).fetchone()
            )

    def test_crates_reject_insufficient_funds(self):
        with self.assertRaises(InsufficientFunds):
            open_crate(100, "Jogadora", self.database_path, rng=random.Random(1))


if __name__ == "__main__":
    unittest.main()
