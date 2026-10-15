import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

import pandas as pd

from src.db import connect
from src.import_tm import POSITION_MAP, import_transfermarkt


class TransfermarktImportTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.players_path = self.root / "players.csv.gz"
        self.clubs_path = self.root / "clubs.csv.gz"
        self.database_path = self.root / "memi.sqlite"
        self._write_inputs()

    def tearDown(self):
        self.temporary_directory.cleanup()

    def _write_inputs(self):
        players = pd.DataFrame(
            [
                {
                    "player_id": "p-1",
                    "first_name": "Ana",
                    "last_name": "Silva",
                    "name": "Ana Silva",
                    "current_club_id": "c-1",
                    "country_of_citizenship": "Brazil",
                    "date_of_birth": "2000-01-02",
                    "sub_position": "Attacking Midfield",
                    "position": "Midfield",
                    "market_value_in_eur": 5000000,
                    "image_url": "https://example.test/ana.jpg",
                    "last_season": 2025,
                },
                {
                    "player_id": "p-2",
                    "first_name": "Bia",
                    "last_name": "Souza",
                    "name": "Bia Souza",
                    "current_club_id": None,
                    "country_of_citizenship": "Brazil",
                    "date_of_birth": None,
                    "sub_position": None,
                    "position": "Missing",
                    "market_value_in_eur": None,
                    "image_url": None,
                    "last_season": 2025,
                },
            ]
        )
        clubs = pd.DataFrame(
            [
                {
                    "club_id": "c-1",
                    "name": "Clube Teste",
                    "domestic_competition_id": "BR1",
                    "stadium_name": "Estádio Teste",
                    "stadium_seats": 20000,
                    "coach_name": "Técnico Teste",
                }
            ]
        )
        players.to_csv(self.players_path, index=False, compression="gzip")
        clubs.to_csv(self.clubs_path, index=False, compression="gzip")

    def test_maps_and_imports_limited_sample_idempotently(self):
        output = StringIO()
        with redirect_stdout(output):
            first = import_transfermarkt(
                self.players_path,
                self.clubs_path,
                self.database_path,
                limit=1,
            )
            second = import_transfermarkt(
                self.players_path,
                self.clubs_path,
                self.database_path,
                limit=1,
            )

        self.assertIn("Attacking Midfield", output.getvalue())
        self.assertEqual(
            first,
            {
                "clubs": 1,
                "players": 1,
                "missing_sub_position": 0,
                "position_fallback": 0,
            },
        )
        self.assertEqual(second["players"], 1)
        with connect(self.database_path) as connection:
            player = connection.execute("SELECT * FROM players").fetchone()
            self.assertEqual(player["name"], "Ana Silva")
            self.assertEqual(player["game_position"], "MEI")
            self.assertEqual(player["market_value_eur"], 5000000)
            self.assertEqual(player["season_last_active"], 2025)
            self.assertEqual(player["activity_status"], "unknown")
            self.assertEqual(player["phase2_candidate"], 1)
            self.assertEqual(player["needs_position_review"], 0)
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM players").fetchone()[0], 1
            )
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM player_sources").fetchone()[0],
                1,
            )
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM clubs").fetchone()[0], 1
            )

    def test_preserves_broad_position_when_sub_position_is_missing(self):
        result = import_transfermarkt(
            self.players_path,
            self.clubs_path,
            self.database_path,
            limit=2,
            show_positions=False,
        )
        self.assertEqual(result["missing_sub_position"], 1)
        self.assertEqual(result["position_fallback"], 0)
        with connect(self.database_path) as connection:
            player = connection.execute(
                "SELECT * FROM players WHERE name = ?", ("Bia Souza",)
            ).fetchone()
            self.assertEqual(player["original_position"], "Missing")
            self.assertIsNone(player["game_position"])
            self.assertEqual(player["needs_position_review"], 1)

    def test_general_position_fallback_is_marked_for_review(self):
        from src.db import initialize_database

        initialize_database(self.database_path)
        with connect(self.database_path) as connection:
            connection.execute(
                """
                INSERT INTO clubs (name, transfermarkt_id)
                VALUES (?, ?)
                """,
                ("Clube Teste", "c-1"),
            )
        frame = pd.read_csv(self.players_path, compression="gzip")
        frame.loc[0, "sub_position"] = None
        frame.loc[0, "position"] = "Defender"
        frame.to_csv(self.players_path, index=False, compression="gzip")
        result = import_transfermarkt(
            self.players_path,
            self.clubs_path,
            self.database_path,
            limit=1,
            show_positions=False,
        )
        self.assertEqual(result["position_fallback"], 1)
        with connect(self.database_path) as connection:
            player = connection.execute("SELECT * FROM players").fetchone()
        self.assertEqual(player["game_position"], "ZAG")
        self.assertEqual(player["needs_position_review"], 1)

    def test_unknown_sub_position_stops_before_import(self):
        frame = pd.read_csv(self.players_path, compression="gzip")
        frame.loc[0, "sub_position"] = "Unreviewed Position"
        frame.to_csv(self.players_path, index=False, compression="gzip")

        with self.assertRaisesRegex(ValueError, "Unreviewed Position"):
            import_transfermarkt(
                self.players_path,
                self.clubs_path,
                self.database_path,
                limit=1,
                show_positions=False,
            )
        self.assertFalse(self.database_path.exists())

    def test_all_reviewed_sub_positions_have_valid_game_position(self):
        self.assertTrue(POSITION_MAP)
        self.assertTrue(
            set(POSITION_MAP.values()).issubset(
                {"GOL", "ZAG", "LD", "LE", "MC", "MEI", "PE", "PD", "CA"}
            )
        )

    def test_rejects_zero_limit(self):
        with self.assertRaisesRegex(ValueError, "--limit"):
            import_transfermarkt(
                self.players_path,
                self.clubs_path,
                self.database_path,
                limit=0,
                show_positions=False,
            )


if __name__ == "__main__":
    unittest.main()
