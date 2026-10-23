from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import random
import tempfile
import unittest
from pathlib import Path

from src.db import connect, initialize_database
from src.economy import EconomyError, activate_rolls, add_wish
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

    def test_claim_cooldown_resets_with_roll_cycle(self):
        p1 = self.add_player("Jogador Base", overall=50)
        p2 = self.add_player("Jogador Comum", overall=65)
        p3 = self.add_player("Jogador Incomum", overall=75)
        register_user(100, "Roller", self.database_path)
        register_user(200, "Claimer", self.database_path)

        # Oferta 1 às 12:10 (ciclo das 12:00)
        with connect(self.database_path) as conn:
            from src.rolls import _create_offer

            o1 = _create_offer(conn, p1, "100", "player_claim", None, NOW)
            o2 = _create_offer(conn, p2, "100", "player_claim", None, NOW + timedelta(minutes=10))
            o3 = _create_offer(
                conn,
                p3,
                "100",
                "player_claim",
                None,
                datetime(2026, 10, 18, 13, 5, tzinfo=timezone.utc),
            )

        # Primeiro claim no ciclo das 12:00
        c1 = claim_roll_offer(o1, 200, self.database_path, now=NOW + timedelta(seconds=10))
        self.assertEqual(c1.status, "player_claimed")

        # Segundo claim no mesmo ciclo (12:20) deve dar cooldown até o próximo ciclo (12:30)
        c2 = claim_roll_offer(
            o2, 200, self.database_path, now=NOW + timedelta(minutes=10, seconds=10)
        )
        self.assertEqual(c2.status, "cooldown")
        self.assertEqual(c2.retry_at, datetime(2026, 10, 18, 12, 30, tzinfo=timezone.utc))

        # No ciclo seguinte (13:05, quando os rolls renovam), o claim volta a ficar livre
        c3 = claim_roll_offer(
            o3, 200, self.database_path, now=datetime(2026, 10, 18, 13, 5, 10, tzinfo=timezone.utc)
        )
        self.assertEqual(c3.status, "player_claimed")

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
        # Ciclos de 30 minutos na hora cheia e na meia hora: 12:10 renova às 12:30.
        self.assertEqual(error.exception.reset_at, NOW + timedelta(minutes=20))

        next_cycle = roll_player(
            100,
            "Pessoa",
            self.database_path,
            now=NOW + timedelta(minutes=20),
            rng=random.Random(12),
        )
        self.assertEqual(next_cycle.rolls_remaining, 9)

    def test_roll_upgrade_adds_a_permanent_roll_to_each_cycle(self):
        self.add_player()
        register_user(100, "Pessoa", self.database_path)
        with connect(self.database_path) as connection:
            connection.execute("""
                INSERT INTO user_upgrades (discord_user_id, upgrade_key, level)
                VALUES ('100', 'rolls', 1)
                """)
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

    def store_rolls(self, amount):
        with connect(self.database_path) as connection:
            connection.execute(
                "INSERT INTO user_upgrades (discord_user_id, upgrade_key, roll_tokens) "
                "VALUES ('100', 'rolls', ?)",
                (amount,),
            )

    def test_stored_crate_rolls_only_count_after_activation(self):
        self.add_player()
        register_user(100, "Pessoa", self.database_path)
        self.store_rolls(1)
        for index in range(10):
            roll_player(100, "Pessoa", self.database_path, now=NOW, rng=random.Random(index))
        with self.assertRaises(RollLimitReached):  # guardado ainda não vale
            roll_player(100, "Pessoa", self.database_path, now=NOW, rng=random.Random(11))
        result = activate_rolls(100, "Pessoa", self.database_path)
        self.assertEqual((result.activated, result.claims_gained, result.carry), (1, 0, 1))
        final_roll = roll_player(100, "Pessoa", self.database_path, now=NOW, rng=random.Random(12))
        self.assertEqual(final_roll.rolls_remaining, 0)
        with self.assertRaises(RollLimitReached):
            roll_player(100, "Pessoa", self.database_path, now=NOW, rng=random.Random(13))
        with self.assertRaises(EconomyError):  # nada mais guardado
            activate_rolls(100, "Pessoa", self.database_path)

    def test_every_ten_activated_rolls_give_one_extra_claim(self):
        register_user(100, "Pessoa", self.database_path)
        self.store_rolls(25)
        first = activate_rolls(100, "Pessoa", self.database_path, 15)
        self.assertEqual((first.claims_gained, first.carry, first.bonus_claims), (1, 5, 1))
        second = activate_rolls(100, "Pessoa", self.database_path)  # os 10 que sobraram
        self.assertEqual((second.activated, second.claims_gained, second.carry), (10, 1, 5))
        self.assertEqual((second.bonus_claims, second.active_rolls), (2, 25))
        with self.assertRaises(EconomyError):
            activate_rolls(100, "Pessoa", self.database_path, 1)

    def test_extra_claim_is_used_only_after_the_cycle_claim(self):
        for name in ("Um", "Dois", "Tres"):
            self.add_player(name=name)
        register_user(100, "Pessoa", self.database_path)
        self.store_rolls(10)
        activate_rolls(100, "Pessoa", self.database_path)
        statuses = []
        for index in range(3):
            rolled = roll_player(
                100, "Pessoa", self.database_path, now=NOW, rng=random.Random(index)
            )
            outcome = claim_roll_offer(rolled.offer_id, 100, self.database_path, now=NOW)
            statuses.append(outcome.status)
        # 1º: o claim do ciclo; 2º: a contratação extra; 3º: acabou, espera o ciclo.
        self.assertEqual(statuses[:2], ["player_claimed", "player_claimed"])
        self.assertNotEqual(statuses[2], "player_claimed")
        with connect(self.database_path) as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT bonus_claims FROM game_users WHERE discord_user_id = '100'"
                ).fetchone()["bonus_claims"],
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
