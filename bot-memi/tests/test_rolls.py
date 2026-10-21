from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import random
import tempfile
import unittest
from pathlib import Path

from src.db import connect, initialize_database
from src.economy import add_wish
from src.game import claim_player, get_wallet, register_user
from src.rolls import (
    RollLimitReached,
    claim_roll_offer,
    roll_player,
)

NOW = datetime(2026, 10, 18, 12, 10, tzinfo=timezone.utc)


class RollTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary_directory.name) / "memi.sqlite"
        initialize_database(self.database_path)

    def tearDown(self):
        self.temporary_directory.cleanup()

    def add_player(self, name="Jogador de teste", overall=80):
        with connect(self.database_path) as connection:
            return connection.execute(
                """
                INSERT INTO players (
                    name, display_name, club_name, game_position, overall,
                    activity_status, phase2_candidate
                ) VALUES (?, ?, 'Clube de teste', 'CA', ?, 'active', 0)
                """,
                (name, name, overall),
            ).lastrowid

    def test_unowned_player_can_be_claimed_and_claim_has_one_hour_cooldown(self):
        player_id = self.add_player()
        register_user(100, "Roller", self.database_path)
        register_user(200, "Outra pessoa", self.database_path)
        result = roll_player(
            100,
            "Roller",
            self.database_path,
            now=NOW,
            rng=random.Random(1),
        )

        early = claim_roll_offer(
            result.offer_id,
            200,
            self.database_path,
            now=NOW + timedelta(seconds=4),
        )
        self.assertEqual(early.status, "exclusive")

        claimed = claim_roll_offer(
            result.offer_id,
            200,
            self.database_path,
            now=NOW + timedelta(seconds=5),
        )
        self.assertEqual(claimed.status, "player_claimed")
        with connect(self.database_path) as connection:
            owner = connection.execute(
                "SELECT discord_user_id FROM player_ownership WHERE player_id = ?",
                (player_id,),
            ).fetchone()
            last_claim = connection.execute(
                "SELECT last_claim_at FROM game_users WHERE discord_user_id = '200'"
            ).fetchone()
        self.assertEqual(owner["discord_user_id"], "200")
        self.assertEqual(
            last_claim["last_claim_at"],
            (NOW + timedelta(seconds=5)).isoformat(timespec="seconds"),
        )

    def test_rerolling_own_player_adds_ball_overall_and_memilets(self):
        player_id = self.add_player(overall=80)
        register_user(100, "Pessoa", self.database_path)
        self.assertTrue(claim_player(player_id, 100, self.database_path))

        result = roll_player(
            100,
            "Pessoa",
            self.database_path,
            now=NOW,
            rng=random.Random(1),
        )

        self.assertEqual(result.kind, "own_duplicate_ball")
        self.assertEqual(result.balls, 1)
        # Overall bruto 80 vira 84 na escala do jogo; a bola soma +1.
        self.assertEqual(result.effective_overall, 85)
        self.assertEqual(result.reward_amount, 100)
        self.assertEqual(get_wallet(100, self.database_path)["memilets"], 100)

    def test_rerolling_after_ten_balls_grants_max_ball_bonus(self):
        player_id = self.add_player()
        register_user(100, "Pessoa", self.database_path)
        self.assertTrue(claim_player(player_id, 100, self.database_path))
        with connect(self.database_path) as connection:
            connection.execute(
                "INSERT INTO player_balls (player_id, balls) VALUES (?, 10)",
                (player_id,),
            )

        result = roll_player(
            100,
            "Pessoa",
            self.database_path,
            now=NOW,
            rng=random.Random(1),
        )

        self.assertEqual(result.kind, "own_duplicate_max_balls")
        self.assertEqual(result.balls, 10)
        self.assertEqual(result.reward_amount, 500)
        self.assertEqual(get_wallet(100, self.database_path)["memilets"], 500)
        with connect(self.database_path) as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT balls FROM player_balls WHERE player_id = ?",
                    (player_id,),
                ).fetchone()["balls"],
                10,
            )

    def test_other_users_duplicate_reward_is_claimed_once_by_roller(self):
        player_id = self.add_player()
        register_user(100, "Dono", self.database_path)
        register_user(200, "Roller", self.database_path)
        self.assertTrue(claim_player(player_id, 100, self.database_path))

        result = roll_player(
            200,
            "Roller",
            self.database_path,
            now=NOW,
            rng=random.Random(3),
        )
        self.assertEqual(result.kind, "other_duplicate")
        self.assertGreaterEqual(result.reward_amount, 100)
        self.assertLessEqual(result.reward_amount, 300)
        self.assertEqual(get_wallet(200, self.database_path)["memilets"], 0)

        claimed = claim_roll_offer(
            result.offer_id,
            200,
            self.database_path,
            now=NOW + timedelta(seconds=1),
        )
        second_claim = claim_roll_offer(
            result.offer_id,
            200,
            self.database_path,
            now=NOW + timedelta(seconds=2),
        )

        self.assertEqual(claimed.status, "reward_claimed")
        self.assertEqual(claimed.reward_amount, result.reward_amount)
        self.assertEqual(second_claim.status, "already_claimed")
        self.assertEqual(
            get_wallet(200, self.database_path)["memilets"],
            result.reward_amount,
        )

    def test_only_ten_rolls_are_available_per_half_hour_cycle(self):
        self.add_player()
        for index in range(10):
            roll_player(
                100,
                "Pessoa",
                self.database_path,
                now=NOW,
                rng=random.Random(index),
            )

        with self.assertRaises(RollLimitReached) as error:
            roll_player(
                100,
                "Pessoa",
                self.database_path,
                now=NOW,
                rng=random.Random(11),
            )
        self.assertEqual(error.exception.reset_at, NOW + timedelta(minutes=30))

        next_cycle = roll_player(
            100,
            "Pessoa",
            self.database_path,
            now=NOW + timedelta(minutes=30),
            rng=random.Random(12),
        )
        self.assertEqual(next_cycle.rolls_remaining, 9)

    def test_roll_upgrade_adds_a_permanent_roll_to_each_cycle(self):
        self.add_player()
        register_user(100, "Pessoa", self.database_path)
        with connect(self.database_path) as connection:
            connection.execute(
                """
                INSERT INTO user_upgrades (discord_user_id, upgrade_key, level)
                VALUES ('100', 'rolls', 1)
                """
            )
        for index in range(11):
            result = roll_player(
                100,
                "Pessoa",
                self.database_path,
                now=NOW,
                rng=random.Random(index),
            )
        self.assertEqual(result.rolls_remaining, 0)
        with self.assertRaises(RollLimitReached) as error:
            roll_player(100, "Pessoa", self.database_path, now=NOW)
        self.assertEqual(error.exception.capacity, 11)

    def test_crate_roll_token_extends_rolls_after_permanent_capacity(self):
        self.add_player()
        register_user(100, "Pessoa", self.database_path)
        with connect(self.database_path) as connection:
            connection.execute(
                """
                INSERT INTO user_upgrades
                    (discord_user_id, upgrade_key, roll_tokens)
                VALUES ('100', 'rolls', 1)
                """
            )
        for index in range(10):
            roll_player(
                100,
                "Pessoa",
                self.database_path,
                now=NOW,
                rng=random.Random(index),
            )
        final_roll = roll_player(
            100,
            "Pessoa",
            self.database_path,
            now=NOW,
            rng=random.Random(12),
        )
        self.assertEqual(final_roll.rolls_remaining, 0)
        with connect(self.database_path) as connection:
            self.assertEqual(
                connection.execute(
                    """
                    SELECT roll_tokens FROM user_upgrades
                    WHERE discord_user_id = '100' AND upgrade_key = 'rolls'
                    """
                ).fetchone()["roll_tokens"],
                0,
            )

    def test_star_wish_relative_bonus_can_select_target_and_wish_claims_bypass_window(self):
        target_id = self.add_player(name="Alvo da Star Wish")
        self.add_player(name="Outro jogador")
        register_user(100, "Pessoa", self.database_path)
        register_user(200, "Wish holder", self.database_path)
        add_wish(100, target_id, "star", self.database_path)
        add_wish(200, target_id, "normal", self.database_path)

        class FirstChoiceRandom(random.Random):
            def random(self):
                return 0.0

        roll = roll_player(
            100,
            "Pessoa",
            self.database_path,
            now=NOW,
            rng=FirstChoiceRandom(1),
        )
        self.assertEqual(roll.player["id"], target_id)
        claimed = claim_roll_offer(
            roll.offer_id,
            200,
            self.database_path,
            now=NOW + timedelta(seconds=1),
        )
        self.assertEqual(claimed.status, "player_claimed")

    def test_simultaneous_roll_claims_award_the_player_once(self):
        player_id = self.add_player()
        register_user(100, "Roller", self.database_path)
        register_user(200, "Claimant A", self.database_path)
        register_user(300, "Claimant B", self.database_path)
        roll = roll_player(
            100,
            "Roller",
            self.database_path,
            now=NOW,
            rng=random.Random(1),
        )

        def claim(user_id):
            return claim_roll_offer(
                roll.offer_id,
                user_id,
                self.database_path,
                now=NOW + timedelta(seconds=5),
            ).status

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(executor.map(claim, (200, 300)))

        self.assertEqual(outcomes.count("player_claimed"), 1)
        self.assertEqual(
            sum(outcome in {"already_claimed", "unavailable"} for outcome in outcomes),
            1,
        )
        with connect(self.database_path) as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM player_ownership WHERE player_id = ?",
                    (player_id,),
                ).fetchone()[0],
                1,
            )


if __name__ == "__main__":
    unittest.main()
