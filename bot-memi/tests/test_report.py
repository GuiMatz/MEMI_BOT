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
        self.assertEqual(report["cross_source_players"], 0)
        self.assertEqual(report["pending_match_candidates"], 0)
        self.assertEqual(report["duplicate_transfermarkt_ids"], 0)
        self.assertEqual(report["duplicate_name_birth_groups"], 0)
        self.assertEqual(report["duplicate_name_birth_players"], 0)
        self.assertEqual(report["ea_matched"], 0)
        self.assertEqual(report["ea_ambiguous"], 0)
        self.assertEqual(report["ea_new_players"], 0)
        self.assertEqual(report["ea_brazil_excluded"], 0)
        self.assertEqual(report["positions"], {"(não mapeada)": 1, "CA": 1, "GOL": 1})
        self.assertEqual(report["players_without_position"], 1)
        self.assertEqual(report["overall_bands"]["80-86"], 0)
        self.assertEqual(report["overall_bands"]["87-89"], 2)
        self.assertEqual(report["overall_bands"]["90-94"], 0)
        self.assertEqual(report["overall_bands"]["98-99"], 0)
        self.assertEqual(report["overall_bands"]["sem overall"], 1)
        self.assertEqual(report["overall_87_plus"], 2)
        self.assertEqual(report["position_review"], 0)
        self.assertEqual(report["inactive_players"], 0)
        self.assertEqual(report["unknown_activity"], 3)
        self.assertEqual(report["phase2_candidates"], 3)
        self.assertEqual(report["roleta_eligible"], 0)
        self.assertEqual(report["players_without_photo"], 2)
        self.assertEqual(report["clubs_without_players"], 0)
        self.assertEqual(len(report["top_overall"]), 2)
        self.assertEqual(len(report["top_market_value"]), 0)
        self.assertEqual(report["clubs_without_players_list"], [])

    def test_empty_database_reports_zero_counts(self):
        report = build_report(self.database_path)
        self.assertEqual(report["players_total"], 0)
        self.assertEqual(report["source_counts"], {})
        self.assertEqual(report["cross_source_players"], 0)
        self.assertEqual(report["pending_match_candidates"], 0)
        self.assertEqual(report["duplicate_transfermarkt_ids"], 0)
        self.assertEqual(report["duplicate_name_birth_groups"], 0)
        self.assertEqual(report["duplicate_name_birth_players"], 0)
        self.assertEqual(report["ea_matched"], 0)
        self.assertEqual(report["ea_ambiguous"], 0)
        self.assertEqual(report["ea_new_players"], 0)
        self.assertEqual(report["ea_brazil_excluded"], 0)
        self.assertEqual(report["positions"], {})
        self.assertEqual(report["players_without_position"], 0)
        self.assertEqual(report["position_review"], 0)
        self.assertEqual(report["overall_87_plus"], 0)
        self.assertEqual(report["inactive_players"], 0)
        self.assertEqual(report["unknown_activity"], 0)
        self.assertEqual(report["phase2_candidates"], 0)
        self.assertEqual(report["roleta_eligible"], 0)
        self.assertEqual(report["players_without_photo"], 0)
        self.assertEqual(report["clubs_without_players"], 0)
        self.assertEqual(report["clubs_without_players_list"], [])
        self.assertEqual(report["top_overall"], [])
        self.assertEqual(report["top_market_value"], [])
        self.assertEqual(
            report["overall_bands"],
            {
                "1-59": 0,
                "60-69": 0,
                "70-79": 0,
                "80-86": 0,
                "87-89": 0,
                "90-94": 0,
                "95-97": 0,
                "98-99": 0,
                "sem overall": 0,
            },
        )

    def test_roleta_count_excludes_players_without_game_position(self):
        with connect(self.database_path) as connection:
            connection.execute("""
                INSERT INTO players (
                    name, overall, activity_status, phase2_candidate
                ) VALUES ('Sem posição', 70, 'active', 0)
                """)
            connection.execute("""
                INSERT INTO ea_import_stats (
                    source, matched, ambiguous, new_players, brazil_excluded
                ) VALUES ('ea_fc27', 3, 2, 4, 1)
                """)

        report = build_report(self.database_path)

        self.assertEqual(report["roleta_eligible"], 0)
        self.assertEqual(report["ea_matched"], 3)
        self.assertEqual(report["ea_ambiguous"], 2)
        self.assertEqual(report["ea_new_players"], 4)
        self.assertEqual(report["ea_brazil_excluded"], 1)


if __name__ == "__main__":
    unittest.main()
