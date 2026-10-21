import tempfile
import unittest
from pathlib import Path

from src.db import connect, initialize_database
from src.ratings import (
    CARTOLA_OVERALL,
    RARITY_TIERS,
    effective_overall,
    game_overall,
    rarity_index,
    rarity_label,
)


class GameOverallTests(unittest.TestCase):
    def test_low_ratings_are_valued_and_top_ratings_stay(self):
        self.assertEqual(game_overall(45), 63)
        self.assertEqual(game_overall(60), 72)
        self.assertEqual(game_overall(75), 81)
        self.assertEqual(game_overall(90), 90)
        self.assertEqual(game_overall(99), 99)
        previous = 0
        for raw in range(1, 100):
            with self.subTest(raw=raw):
                value = game_overall(raw)
                self.assertGreaterEqual(value, raw)
                self.assertGreaterEqual(value, previous)
                previous = value

    def test_cartola_players_are_90_except_maria_sofia(self):
        self.assertEqual(game_overall(40, "Cartola", "Fulano"), CARTOLA_OVERALL)
        self.assertEqual(game_overall(40, "Cartola", "Maria Sofia"), 100)
        self.assertEqual(game_overall(100, "EA", "Outra Pessoa"), 99)
        self.assertIsNone(game_overall(None, "Cartola", "Fulano"))

    def test_only_the_special_card_reaches_100_with_balls(self):
        self.assertEqual(effective_overall(95, 10), 99)
        self.assertEqual(effective_overall(100, 3), 100)
        self.assertEqual(effective_overall(80, 1), 81)

    def test_rarity_tiers_cover_every_overall_once(self):
        for overall in range(1, 101):
            with self.subTest(overall=overall):
                index = rarity_index(overall)
                lower, upper = RARITY_TIERS[index][2:4]
                self.assertTrue(lower <= overall <= upper)
        self.assertEqual(rarity_label(100), "👑 Única")
        self.assertEqual(rarity_label(None), "—")

    def test_sql_functions_match_python(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "ratings.sqlite"
            initialize_database(database)
            with connect(database) as connection:
                row = connection.execute(
                    "SELECT game_overall(60, 'EA', 'X') AS a, "
                    "game_overall(10, 'Cartola', 'Maria  Sofia') AS b, "
                    "effective_overall(99, 5) AS c, rarity_index(100) AS d"
                ).fetchone()
        self.assertEqual(tuple(row), (72, 100, 99, len(RARITY_TIERS) - 1))


if __name__ == "__main__":
    unittest.main()
