from datetime import datetime, timezone
import random
import tempfile
import unittest
from pathlib import Path

from src.championship import (
    ChampionshipError,
    get_championship,
    play_championship,
    register_team,
)
from src.db import connect, initialize_database
from src.game import claim_player, register_user
from src.teams import FORMATIONS, TeamError, ensure_team, scale_player

NOW = datetime(2026, 10, 18, 16, 30, tzinfo=timezone.utc)


class ChampionshipTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.tempdir.name) / "league.sqlite"
        initialize_database(self.database_path)

    def tearDown(self):
        self.tempdir.cleanup()

    def add_team(self, user_id, rating):
        register_user(user_id, f"Pessoa {user_id}", self.database_path)
        ensure_team(user_id, f"Pessoa {user_id}", self.database_path)
        for index, position in enumerate(FORMATIONS["4-3-3"]):
            with connect(self.database_path) as connection:
                player_id = connection.execute(
                    """
                    INSERT INTO players (
                        name, display_name, game_position, overall,
                        activity_status, phase2_candidate
                    ) VALUES (?, ?, ?, ?, 'active', 0)
                    """,
                    (
                        f"Jogador {user_id}-{index}",
                        f"Jogador {index}",
                        position,
                        rating,
                    ),
                ).lastrowid
            claim_player(player_id, user_id, self.database_path)
            scale_player(user_id, player_id, self.database_path)

    def test_all_teams_play_once_and_top_two_play_final_last(self):
        for user_id, rating in ((100, 88), (200, 80), (300, 72)):
            self.add_team(user_id, rating)
            register_team(
                555,
                user_id,
                now=NOW,
                database_path=self.database_path,
            )

        result = play_championship(
            555,
            now=NOW,
            rng=random.Random(314),
            database_path=self.database_path,
        )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(result["entries"]), 3)
        group = [fixture for fixture in result["fixtures"] if not fixture["is_final"]]
        final = [fixture for fixture in result["fixtures"] if fixture["is_final"]]
        self.assertEqual(len(group), 3)
        self.assertEqual(len(final), 1)
        self.assertEqual(final[0]["match_order"], 999999)
        self.assertIsNotNone(final[0]["winner_user_id"])
        self.assertEqual(
            len({(fixture["home_user_id"], fixture["away_user_id"]) for fixture in group}),
            3,
        )
        refreshed = get_championship(
            555,
            now=NOW,
            database_path=self.database_path,
        )
        self.assertEqual(refreshed["status"], "completed")

    def test_registration_requires_valid_team_and_duplicate_daily_run_is_rejected(self):
        register_user(100, "Pessoa", self.database_path)
        ensure_team(100, "Pessoa", self.database_path)
        with self.assertRaises(TeamError):
            register_team(
                555,
                100,
                now=NOW,
                database_path=self.database_path,
            )
        self.add_team(100, 85)
        register_team(555, 100, now=NOW, database_path=self.database_path)
        with self.assertRaises(ChampionshipError):
            play_championship(
                555,
                now=NOW,
                rng=random.Random(2),
                database_path=self.database_path,
            )


if __name__ == "__main__":
    unittest.main()
