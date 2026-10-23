import tempfile
import unittest
from pathlib import Path

from src.db import connect, initialize_database
from src.game import get_catalog_entries, search_players, top_player_cards
from src.pool_cache import snapshot


class PoolCacheTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.tempdir.name) / "pool.sqlite"
        initialize_database(self.database_path)
        with connect(self.database_path) as connection:
            for name, overall in (("Ronaldo", 90), ("Ronaldinho", 92), ("Zé", 60)):
                connection.execute(
                    "INSERT INTO players (name, display_name, game_position, overall, "
                    "activity_status, phase2_candidate) VALUES (?, ?, 'CA', ?, 'active', 0)",
                    (name, name, overall),
                )

    def tearDown(self):
        self.tempdir.cleanup()

    def test_snapshot_is_reused_and_rebuilt_when_players_change(self):
        with connect(self.database_path) as connection:
            first = snapshot(connection, self.database_path)
            self.assertIs(snapshot(connection, self.database_path), first)
            connection.execute("UPDATE players SET overall = 99 WHERE name = 'Zé'")
            second = snapshot(connection, self.database_path)
        self.assertIsNot(second, first)
        self.assertEqual(top_player_cards(self.database_path, 1)[0]["name"], "Zé")

    def test_inactive_players_leave_the_pool(self):
        with connect(self.database_path) as connection:
            connection.execute("UPDATE players SET activity_status = 'inactive' WHERE name = 'Zé'")
        entries, total = get_catalog_entries("jogadores", self.database_path, limit=10)
        self.assertEqual(total, 2)
        self.assertEqual([entry["name"] for entry in entries], ["Ronaldinho", "Ronaldo"])

    def test_search_keeps_exact_name_first_then_overall(self):
        names = [player["name"] for player in search_players("ronaldo", self.database_path)]
        self.assertEqual(names, ["Ronaldo"])
        names = [player["name"] for player in search_players("ronald", self.database_path)]
        self.assertEqual(names, ["Ronaldinho", "Ronaldo"])  # sem nome exato: maior overall


if __name__ == "__main__":
    unittest.main()
