import random
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from src.compare import compare_image, compare_rows, forecast, head_to_head, snapshot
from src.db import connect, initialize_database
from src.game import claim_player, register_user
from src.teams import FORMATIONS, TeamError, ensure_team, scale_player


class CompareTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.tempdir.name) / "c.sqlite"
        initialize_database(self.database_path)

    def tearDown(self):
        self.tempdir.cleanup()

    def team(self, user_id, overall, full=True):
        register_user(user_id, f"P{user_id}", self.database_path)
        ensure_team(user_id, f"Time {user_id}", self.database_path)
        positions = FORMATIONS["4-3-3"] if full else FORMATIONS["4-3-3"][:5]
        for index, position in enumerate(positions):
            with connect(self.database_path) as connection:
                player_id = connection.execute(
                    "INSERT INTO players (name, game_position, overall, activity_status, "
                    "phase2_candidate) VALUES (?, ?, ?, 'active', 0)",
                    (f"J{user_id}-{index}", position, overall),
                ).lastrowid
            claim_player(player_id, user_id, self.database_path)
            scale_player(user_id, player_id, self.database_path)

    def test_stronger_team_wins_the_rows_and_the_forecast(self):
        self.team(1, 85)
        self.team(2, 60)
        strong, weak = snapshot(1, self.database_path), snapshot(2, self.database_path)
        rows = dict((label, (a, b)) for label, a, b in compare_rows(strong, weak))
        self.assertEqual(list(rows)[:5], ["Força", "Ataque", "Defesa", "Meio", "Goleiro"])
        self.assertGreater(rows["Força"][0], rows["Força"][1])
        win, draw, loss = forecast(strong, weak, 200, random.Random(1))
        self.assertAlmostEqual(win + draw + loss, 1.0)
        self.assertGreater(win, loss)
        self.assertEqual(head_to_head(1, 2, self.database_path), (0, 0, 0, 0, 0))
        image = Image.open(compare_image(strong, weak))
        self.assertGreater(image.width, image.height)  # as duas escalações lado a lado

    def test_incomplete_team_has_no_forecast_and_missing_team_errors(self):
        self.team(1, 80)
        self.team(2, 80, full=False)
        full, partial = snapshot(1, self.database_path), snapshot(2, self.database_path)
        self.assertFalse(partial.complete)
        self.assertIsNone(forecast(full, partial))
        self.assertEqual([label for label, *_ in compare_rows(full, partial)], ["Força", "Elenco"])
        with self.assertRaises(TeamError):
            snapshot(3, self.database_path)

    def test_embed_marks_the_better_side(self):
        from src.cogs.matches import compare_embed

        self.team(1, 85)
        self.team(2, 60)
        strong, weak = snapshot(1, self.database_path), snapshot(2, self.database_path)
        embed = compare_embed(strong, weak, (0.7, 0.2, 0.1), (1, 0, 0, 3, 1))
        self.assertIn("▲", embed.description)
        names = [field.name for field in embed.fields]
        self.assertIn("🤝 Confronto direto", names)
        self.assertIn("70%", embed.fields[-1].value)


if __name__ == "__main__":
    unittest.main()
