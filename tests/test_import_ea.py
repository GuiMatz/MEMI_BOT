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
            self.assertEqual(player["overall_source"], "ea_fc25")
            self.assertEqual(source["source_club_name"], "Clube EA")
            self.assertEqual(source["source_position"], "ST, LW")

    def test_import_is_idempotent_for_same_source_and_id(self):
        import_ea([self.fc25_path], self.database_path, limit=1, show_positions=False)
        import_ea([self.fc25_path], self.database_path, limit=1, show_positions=False)
        with connect(self.database_path) as connection:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM players").fetchone()[0], 1
            )
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM player_sources").fetchone()[0],
                1,
            )

    def test_different_ea_sources_are_not_merged(self):
        fc24_path = self.root / "ea_fc24.csv"
        frame = pd.read_csv(self.fc25_path)
        frame.to_csv(fc24_path, index=False)
        import_ea([self.fc25_path, fc24_path], self.database_path, limit=1)
        with connect(self.database_path) as connection:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM players").fetchone()[0], 2
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


if __name__ == "__main__":
    unittest.main()
