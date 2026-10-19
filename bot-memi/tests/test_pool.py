import tempfile
import unittest
from pathlib import Path

from src.db import connect, initialize_database
from src.pool import get_roleta_pool


class RoletaPoolTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary_directory.name) / "memi.sqlite"
        initialize_database(self.database_path)

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_excludes_unknown_inactive_and_players_without_overall(self):
        with connect(self.database_path) as connection:
            connection.execute("""
                INSERT INTO players (
                    name, game_position, overall, activity_status, phase2_candidate
                ) VALUES ('Ativo', 'CA', 75, 'active', 0)
                """)
            connection.execute("""
                INSERT INTO players (name, overall, activity_status)
                VALUES ('Desconhecido', 80, 'unknown')
                """)
            connection.execute("""
                INSERT INTO players (name, overall, activity_status)
                VALUES ('Inativo', 90, 'inactive')
                """)
            connection.execute("""
                INSERT INTO players (name, activity_status)
                VALUES ('Sem overall', 'active')
                """)
        players = get_roleta_pool(self.database_path)
        self.assertEqual([player["name"] for player in players], ["Ativo"])

    def test_rejects_invalid_limit(self):
        with self.assertRaisesRegex(ValueError, "--limit"):
            get_roleta_pool(self.database_path, limit=0)


if __name__ == "__main__":
    unittest.main()
