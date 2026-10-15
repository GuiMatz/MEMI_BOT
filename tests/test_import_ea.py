import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

import pandas as pd

from src.db import connect
from src.import_ea import POSITION_MAP, import_ea


class EAImportTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.fc25_path = self.root / "ea_fc25.csv"
        self.database_path = self.root / "memi.sqlite"
        self._write_fc25()

    def tearDown(self):
        self.temporary_directory.cleanup()

    def _write_fc25(self, positions=None):
        positions = positions or ["ST, LW", "GK"]
        pd.DataFrame(
            [
                {
                    "player_id": "ea-1",
                    "long_name": "Atacante Teste",
                    "player_positions": positions[0],
                    "overall": 87,
                    "dob": "2001-03-04",
                    "nationality_name": "Brazil",
                    "club_name": "Clube EA",
                    "player_face_url": "https://example.test/ea-1.jpg",
                },
                {
                    "player_id": "ea-2",
                    "long_name": "Goleiro Teste",
                    "player_positions": positions[1],
                    "overall": 79,
                    "dob": None,
                    "nationality_name": "Portugal",
                    "club_name": "Outro Clube",
                    "player_face_url": None,
                },
            ]
        ).to_csv(self.fc25_path, index=False)

    def test_import_maps_primary_position_and_preserves_source_details(self):
        output = StringIO()
        with redirect_stdout(output):
            result = import_ea(
                [self.fc25_path],
                database_path=self.database_path,
                limit=1,
            )
        self.assertIn("ST, LW", output.getvalue())
        self.assertEqual(result["ea_fc25.csv"]["players"], 1)

        with connect(self.database_path) as connection:
            player = connection.execute("SELECT * FROM players").fetchone()
            source = connection.execute("SELECT * FROM player_sources").fetchone()
            self.assertEqual(player["game_position"], "CA")
            self.assertEqual(player["overall"], 87)
            self.assertEqual(player["overall_source"], "EA")
            self.assertEqual(player["overall_edition"], "FC25")
            self.assertEqual(player["activity_status"], "unknown")
            self.assertEqual(player["club_name"], "Clube EA")
            self.assertEqual(source["source_club_name"], "Clube EA")
            self.assertEqual(source["source_position"], "ST, LW")

    def test_import_is_idempotent_for_same_source_and_id(self):
        review_path = self.root / "review.csv"
        import_ea(
            [self.fc25_path],
            self.database_path,
            limit=1,
            show_positions=False,
            review_path=review_path,
        )
        import_ea(
            [self.fc25_path],
            self.database_path,
            limit=1,
            show_positions=False,
            review_path=review_path,
        )
        with connect(self.database_path) as connection:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM players").fetchone()[0], 1
            )
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM player_sources").fetchone()[0],
                1,
            )

    def test_same_player_from_different_editions_is_merged(self):
        fc24_path = self.root / "ea_fc24.csv"
        frame = pd.read_csv(self.fc25_path)
        frame.to_csv(fc24_path, index=False)
        import_ea([self.fc25_path, fc24_path], self.database_path, limit=1)
        with connect(self.database_path) as connection:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM players").fetchone()[0], 1
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(DISTINCT source) FROM player_sources"
                ).fetchone()[0],
                2,
            )

    def test_unknown_position_stops_before_database_creation(self):
        self._write_fc25(["Unreviewed, ST", "GK"])
        with self.assertRaisesRegex(ValueError, "UNREVIEWED"):
            import_ea(
                [self.fc25_path],
                database_path=self.database_path,
                limit=1,
                show_positions=False,
            )
        self.assertFalse(self.database_path.exists())

    def test_overall_outside_1_to_99_is_rejected(self):
        frame = pd.read_csv(self.fc25_path)
        frame.loc[0, "overall"] = 100
        frame.to_csv(self.fc25_path, index=False)
        with self.assertRaisesRegex(ValueError, "fora da faixa"):
            import_ea(
                [self.fc25_path],
                database_path=self.database_path,
                limit=1,
                show_positions=False,
            )
        with connect(self.database_path) as connection:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM players").fetchone()[0], 0
            )

    def test_non_integer_overall_is_rejected(self):
        frame = pd.read_csv(self.fc25_path)
        frame["overall"] = frame["overall"].astype(float)
        frame.loc[0, "overall"] = 87.5
        frame.to_csv(self.fc25_path, index=False)
        with self.assertRaisesRegex(ValueError, "não inteiro"):
            import_ea(
                [self.fc25_path],
                database_path=self.database_path,
                limit=1,
                show_positions=False,
            )

    def test_position_map_matches_requested_game_positions(self):
        self.assertEqual(POSITION_MAP["ST"], "CA")
        self.assertEqual(POSITION_MAP["CAM"], "MEI")
        self.assertEqual(POSITION_MAP["CDM"], "MC")
        self.assertEqual(POSITION_MAP["LWB"], "LE")
        self.assertEqual(POSITION_MAP["RWB"], "LD")
        self.assertEqual(POSITION_MAP["GK"], "GOL")

    def test_fc27_schema_uses_primary_position_and_falls_back_to_full_name(self):
        path = self.root / "players.csv"
        pd.DataFrame(
            [
                {
                    "player_id": 252371,
                    "common_name": None,
                    "first_name": "Jude",
                    "last_name": "Bellingham",
                    "position": "CAM",
                    "alternate_positions": "CM ST",
                    "overall_rating": 90,
                    "edition": "fc27",
                    "snapshot_date": "2026-09-12",
                    "age": 23,
                    "nationality": "England",
                    "club_name": "Real Madrid",
                }
            ]
        ).to_csv(path, index=False)
        result = import_ea([path], self.database_path, show_positions=False)
        self.assertEqual(result["players.csv"]["players"], 1)
        with connect(self.database_path) as connection:
            player = connection.execute("SELECT * FROM players").fetchone()
            source = connection.execute("SELECT * FROM player_sources").fetchone()
        self.assertEqual(player["name"], "Jude Bellingham")
        self.assertEqual(player["game_position"], "MEI")
        self.assertEqual(player["overall_source"], "EA")
        self.assertEqual(player["overall_edition"], "FC27")
        self.assertEqual(player["club_name"], "Real Madrid")
        self.assertEqual(source["source_club_name"], "Real Madrid")

    def test_fc26_edition_is_detected_from_file_name(self):
        path = self.root / "FC26_20250921.csv"
        frame = pd.read_csv(self.fc25_path)
        frame.to_csv(path, index=False)
        import_ea([path], self.database_path, limit=1, show_positions=False)
        with connect(self.database_path) as connection:
            player = connection.execute("SELECT * FROM players").fetchone()
        self.assertEqual(player["overall_edition"], "FC26")
        self.assertEqual(player["overall_source"], "EA")

    def test_latest_edition_wins_regardless_of_input_order(self):
        fc27_path = self.root / "FC27_20260912.csv"
        frame = pd.read_csv(self.fc25_path).iloc[:1].copy()
        frame["overall"] = 91
        frame.to_csv(fc27_path, index=False)
        import_ea(
            [fc27_path, self.fc25_path],
            self.database_path,
            limit=1,
            show_positions=False,
            review_path=self.root / "review.csv",
        )
        with connect(self.database_path) as connection:
            player = connection.execute("SELECT * FROM players").fetchone()
        self.assertEqual(player["overall"], 91)
        self.assertEqual(player["overall_edition"], "FC27")

    def test_age_nationality_match_uses_snapshot_date(self):
        from src.db import initialize_database

        initialize_database(self.database_path)
        with connect(self.database_path) as connection:
            connection.execute("""
                INSERT INTO players (
                    name, birth_date, nationality, tm_player_id, activity_status
                ) VALUES ('Atacante Teste', '2001-03-04', 'Brazil', 'tm-1', 'active')
                """)
        path = self.root / "FC27_20260912.csv"
        pd.DataFrame(
            [
                {
                    "player_id": "ea-age",
                    "long_name": "Atacante Teste",
                    "player_positions": "ST",
                    "overall": 88,
                    "age": 25,
                    "nationality_name": "Brazil",
                    "club_name": "Clube Europeu",
                }
            ]
        ).to_csv(path, index=False)
        result = import_ea(
            [path],
            self.database_path,
            show_positions=False,
            review_path=self.root / "review.csv",
        )
        self.assertEqual(result[path.name]["matched"], 1)
        self.assertEqual(result[path.name]["new_players"], 0)
        with connect(self.database_path) as connection:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM players").fetchone()[0], 1
            )

    def test_ambiguous_match_is_written_to_review_and_not_inserted(self):
        from src.db import initialize_database

        initialize_database(self.database_path)
        with connect(self.database_path) as connection:
            for tm_id in ("tm-1", "tm-2"):
                connection.execute(
                    """
                    INSERT INTO players (name, birth_date, nationality, tm_player_id)
                    VALUES ('Atacante Teste', '2001-03-04', 'Brazil', ?)
                    """,
                    (tm_id,),
                )
        review_path = self.root / "review.csv"
        result = import_ea(
            [self.fc25_path],
            self.database_path,
            limit=1,
            show_positions=False,
            review_path=review_path,
        )
        self.assertEqual(result[self.fc25_path.name]["ambiguous"], 1)
        with connect(self.database_path) as connection:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM players").fetchone()[0], 2
            )
        self.assertIn(
            "multiple_identity_matches", review_path.read_text(encoding="utf-8-sig")
        )

    def test_brazilian_ea_only_player_is_excluded(self):
        path = self.root / "FC27_brazil.csv"
        pd.DataFrame(
            [
                {
                    "player_id": "ea-br",
                    "long_name": "Jogador Brasil",
                    "player_positions": "ST",
                    "overall": 80,
                    "dob": "2000-01-01",
                    "club_name": "Time X",
                    "league_name": "Campeonato Brasileiro Série A",
                }
            ]
        ).to_csv(path, index=False)
        result = import_ea(
            [path],
            self.database_path,
            show_positions=False,
            review_path=self.root / "review.csv",
        )
        self.assertEqual(result[path.name]["brazil_excluded"], 1)
        with connect(self.database_path) as connection:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM players").fetchone()[0], 0
            )


if __name__ == "__main__":
    unittest.main()
