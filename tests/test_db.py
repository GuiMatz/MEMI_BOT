import sqlite3
import tempfile
import unittest
from pathlib import Path

from src.db import connect, initialize_database


class DatabaseSchemaTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary_directory.name) / "test.sqlite"

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_initialization_creates_expected_tables_and_is_idempotent(self):
        initialize_database(self.database_path)
        initialize_database(self.database_path)

        with connect(self.database_path) as connection:
            tables = {
                row["name"]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            self.assertTrue(
                {
                    "players",
                    "clubs",
                    "leagues",
                    "stadiums",
                    "coaches",
                    "player_sources",
                    "overall_adjustments",
                    "league_overall_weights",
                    "player_match_candidates",
                    "import_checkpoints",
                }.issubset(tables)
            )
            self.assertEqual(
                {
                    row["version"]
                    for row in connection.execute("SELECT version FROM schema_version")
                },
                {1, 2, 3, 4, 5},
            )

    def test_player_constraints_reject_invalid_game_position_and_overall(self):
        initialize_database(self.database_path)
        with connect(self.database_path) as connection:
            for position, overall in (("VOL", 50), ("MC", 100)):
                with self.subTest(position=position, overall=overall):
                    with self.assertRaises(sqlite3.IntegrityError):
                        connection.execute(
                            "INSERT INTO players (name, game_position, overall) "
                            "VALUES (?, ?, ?)",
                            ("Jogador de teste", position, overall),
                        )

    def test_source_identity_is_unique(self):
        initialize_database(self.database_path)
        with connect(self.database_path) as connection:
            player_id = connection.execute(
                "INSERT INTO players (name) VALUES (?)", ("Jogador de teste",)
            ).lastrowid
            connection.execute(
                "INSERT INTO player_sources (source, source_id, player_id) "
                "VALUES (?, ?, ?)",
                ("transfermarkt", "123", player_id),
            )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "INSERT INTO player_sources (source, source_id, player_id) "
                    "VALUES (?, ?, ?)",
                    ("transfermarkt", "123", player_id),
                )

    def test_schema_migrates_existing_player_sources(self):
        with connect(self.database_path) as connection:
            connection.execute("""
                CREATE TABLE schema_version (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """)
            connection.execute("INSERT INTO schema_version (version) VALUES (1)")
            connection.execute("""
                CREATE TABLE player_sources (
                    id INTEGER PRIMARY KEY,
                    source TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    player_id INTEGER NOT NULL,
                    source_name TEXT,
                    UNIQUE (source, source_id)
                )
                """)

        initialize_database(self.database_path)
        with connect(self.database_path) as connection:
            columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(player_sources)")
            }
            player_columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(players)")
            }
            versions = {
                row["version"]
                for row in connection.execute("SELECT version FROM schema_version")
            }
            self.assertTrue(
                {
                    "source_club_name",
                    "source_position",
                    "source_season",
                }.issubset(columns)
            )
            self.assertTrue(
                {
                    "overall_edition",
                    "season_last_active",
                    "activity_status",
                    "phase2_candidate",
                    "needs_position_review",
                }.issubset(player_columns)
            )
            self.assertEqual(versions, {1, 2, 3, 4, 5})


if __name__ == "__main__":
    unittest.main()
