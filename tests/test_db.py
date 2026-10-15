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
                    "ea_import_stats",
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
                {1, 2, 3, 4, 5, 6},
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
                    "source_league_name",
                    "source_snapshot_date",
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
            self.assertTrue(
                {"tm_player_id", "display_name", "club_name"}.issubset(player_columns)
            )
            self.assertNotIn("club_id", player_columns)
            self.assertEqual(versions, {1, 2, 3, 4, 5, 6})

    def test_schema_v6_migrates_existing_players_and_preserves_relations(self):
        with connect(self.database_path) as connection:
            connection.executescript("""
                CREATE TABLE schema_version (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                INSERT INTO schema_version (version) VALUES (1), (2), (3), (4), (5);
                CREATE TABLE clubs (
                    id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL
                );
                CREATE TABLE players (
                    id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    birth_date TEXT,
                    nationality TEXT,
                    club_id INTEGER REFERENCES clubs(id) ON DELETE SET NULL,
                    original_position TEXT,
                    game_position TEXT,
                    overall INTEGER,
                    overall_source TEXT,
                    overall_edition TEXT,
                    market_value_eur INTEGER,
                    season_last_active INTEGER,
                    activity_status TEXT NOT NULL DEFAULT 'unknown',
                    phase2_candidate INTEGER NOT NULL DEFAULT 1,
                    needs_position_review INTEGER NOT NULL DEFAULT 0,
                    photo_url TEXT,
                    photo_path TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE player_sources (
                    id INTEGER PRIMARY KEY,
                    source TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    player_id INTEGER NOT NULL REFERENCES players(id) ON DELETE CASCADE,
                    source_name TEXT,
                    source_club_name TEXT,
                    source_position TEXT,
                    source_season INTEGER,
                    UNIQUE (source, source_id)
                );
                CREATE TABLE overall_adjustments (
                    player_id INTEGER PRIMARY KEY REFERENCES players(id) ON DELETE CASCADE,
                    overall INTEGER NOT NULL,
                    reason TEXT NOT NULL
                );
                INSERT INTO clubs (id, name) VALUES (4, 'Clube Teste');
                INSERT INTO players (
                    id, name, birth_date, club_id, activity_status, overall
                ) VALUES (7, 'Jogador', '2000-01-02', 4, 'active', 70);
                INSERT INTO player_sources (source, source_id, player_id)
                VALUES ('transfermarkt', 'tm-123', 7);
                INSERT INTO overall_adjustments (player_id, overall, reason)
                VALUES (7, 75, 'teste de preservação');
                """)

        initialize_database(self.database_path)

        with connect(self.database_path) as connection:
            columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(players)")
            }
            player = connection.execute("SELECT * FROM players WHERE id = 7").fetchone()
            adjustment = connection.execute(
                "SELECT overall FROM overall_adjustments WHERE player_id = 7"
            ).fetchone()
            self.assertNotIn("club_id", columns)
            self.assertEqual(player["tm_player_id"], "tm-123")
            self.assertEqual(player["club_name"], "Clube Teste")
            self.assertEqual(player["display_name"], "Jogador")
            self.assertEqual(adjustment["overall"], 75)
            self.assertEqual(
                connection.execute("PRAGMA foreign_key_check").fetchall(), []
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM player_sources WHERE player_id = ?",
                    (7,),
                ).fetchone()[0],
                1,
            )

    def test_transfermarkt_player_id_is_unique(self):
        initialize_database(self.database_path)
        with connect(self.database_path) as connection:
            connection.execute(
                "INSERT INTO players (name, tm_player_id) VALUES (?, ?)",
                ("Jogador 1", "tm-unique"),
            )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "INSERT INTO players (name, tm_player_id) VALUES (?, ?)",
                    ("Jogador 2", "tm-unique"),
                )


if __name__ == "__main__":
    unittest.main()
