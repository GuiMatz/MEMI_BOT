import tempfile
import unittest
from pathlib import Path

from src.db import connect, initialize_database
from src.overall import apply_current_stars, recalculate_overalls
from src.ratings import game_overall


class CurrentStarsTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.tempdir.name) / "stars.sqlite"
        initialize_database(self.database_path)

    def tearDown(self):
        self.tempdir.cleanup()

    def add(self, name, overall, market=None, source="market_log", display=None):
        with connect(self.database_path) as connection:
            return connection.execute(
                "INSERT INTO players (name, display_name, game_position, overall, overall_source, "
                "market_value_eur, activity_status, phase2_candidate) "
                "VALUES (?, ?, 'CA', ?, ?, ?, 'active', 0)",
                (name, display or name, overall, source, market),
            ).lastrowid

    def game(self, player_id):
        with connect(self.database_path) as connection:
            row = connection.execute(
                "SELECT overall, overall_source, name FROM players WHERE id = ?", (player_id,)
            ).fetchone()
        return game_overall(row["overall"], row["overall_source"], row["name"])

    def test_stars_get_fc27_rating_plus_two(self):
        mbappe = self.add("Kylian Mbappé", 88, 180_000_000)  # travado em 89 pelo mercado
        saka = self.add("Bukayo Saka", 88, 140_000_000)
        modric = self.add("Luka Modrić", 80, 5_000_000)
        report = apply_current_stars(self.database_path)
        self.assertEqual(self.game(mbappe), 93)  # 91 no FC 27
        self.assertEqual(self.game(saka), 89)  # 87 no FC 27
        self.assertEqual(self.game(modric), 87)  # 85 no FC 27
        self.assertEqual(report["applied"], 3)
        self.assertIn("Erling Haaland", report["missing"])

    def test_homonyms_pick_the_most_valuable_and_survive_recalculation(self):
        star = self.add("Vitinha", 88, 110_000_000)
        other = self.add("Vitinha", 70, 3_000_000)
        apply_current_stars(self.database_path)
        self.assertEqual(self.game(star), 92)
        self.assertEqual(self.game(other), game_overall(70))
        recalculate_overalls(self.database_path)  # ajuste manual não é desfeito
        self.assertEqual(self.game(star), 92)

    def test_living_legends_stay_at_99(self):
        messi = self.add("Lionel Messi", 99, 20_000_000, source="Lenda")
        report = apply_current_stars(self.database_path)
        self.assertEqual(self.game(messi), 99)
        self.assertIn("Lionel Messi", report["living_legends"])

    def test_alias_matches_display_name(self):
        vini = self.add(
            "Vinícius José Paixão de Oliveira Júnior", 88, 150_000_000, display="Vini Jr."
        )
        apply_current_stars(self.database_path)
        self.assertEqual(self.game(vini), 91)  # 89 no FC 27


if __name__ == "__main__":
    unittest.main()
