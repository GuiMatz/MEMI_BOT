import tempfile
import unittest
from pathlib import Path

from src.db import connect, initialize_database
from src.report import build_report


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary_directory.name) / "memi.sqlite"
        initialize_database(self.database_path)

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_reports_sources_positions_bands_and_missing_photos(self):
        with connect(self.database_path) as connection:
            player_id = connection.execute(
                """
                INSERT INTO players (
                    name, game_position, overall, photo_path
                ) VALUES (?, ?, ?, ?)
                """,
                ("Atacante", "CA", 87, "players/1.png"),
            ).lastrowid
            connection.execute(
                """
                INSERT INTO player_sources (source, source_id, player_id)
                VALUES (?, ?, ?)
                """,
                ("ea_fc25", "1", player_id),
            )
            connection.execute(
                "INSERT INTO players (name, game_position, overall) VALUES (?, ?, ?)",
                ("Goleiro", "GOL", 88),
            )
            connection.execute("INSERT INTO players (name) VALUES (?)", ("Sem dados",))

        report = build_report(self.database_path)

        self.assertEqual(report["players_total"], 3)
        self.assertEqual(report["source_counts"], {"ea_fc25": 1})
        self.assertEqual(report["positions"], {"(não mapeada)": 1, "CA": 1, "GOL": 1})
        self.assertEqual(report["overall_bands"]["80-87"], 1)
        self.assertEqual(report["overall_bands"]["88-99"], 1)
        self.assertEqual(report["overall_bands"]["sem overall"], 1)
        self.assertEqual(report["players_without_photo"], 2)

    def test_empty_database_reports_zero_counts(self):
        report = build_report(self.database_path)
        self.assertEqual(report["players_total"], 0)
        self.assertEqual(report["source_counts"], {})
        self.assertEqual(report["positions"], {})
        self.assertEqual(report["players_without_photo"], 0)
        self.assertEqual(
            report["overall_bands"],
            {
                "<50": 0,
                "50-59": 0,
                "60-69": 0,
                "70-79": 0,
                "80-87": 0,
                "88-99": 0,
                "sem overall": 0,
            },
        )


if __name__ == "__main__":
    unittest.main()
