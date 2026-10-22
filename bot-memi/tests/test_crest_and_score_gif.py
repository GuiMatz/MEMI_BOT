import tempfile
import unittest
from io import BytesIO
from pathlib import Path

from PIL import Image

from src.crests import (
    CrestError,
    normalize_crest,
    remove_custom_crest,
    save_custom_crest,
    team_logo_path,
)
from src.db import connect, initialize_database
from src.game import register_user
from src.narration import match_embed
from src.score_image import render_score_gif
from src.teams import ensure_team, get_team


def png(size=(300, 200), color=(200, 30, 30, 255), border=0, mode="RGBA", fmt="PNG"):
    image = Image.new(mode, (size[0] + border * 2, size[1] + border * 2), (0, 0, 0, 0))
    image.paste(Image.new(mode, size, color), (border, border))
    buffer = BytesIO()
    image.save(buffer, format=fmt)
    return buffer.getvalue()


class CrestTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.database_path = self.root / "game.sqlite"
        initialize_database(self.database_path)
        register_user(100, "Pessoa", self.database_path)
        ensure_team(100, "Pessoa", self.database_path)

    def tearDown(self):
        self.tempdir.cleanup()

    def test_normalize_trims_transparent_border_and_limits_size(self):
        picture = normalize_crest(png((1000, 600), border=200))
        self.assertEqual(picture.size, (512, 307))
        self.assertEqual(picture.mode, "RGBA")

    def test_rejects_invalid_tiny_and_empty_images(self):
        with self.assertRaises(CrestError):
            normalize_crest(b"isto nao e imagem")
        with self.assertRaises(CrestError):
            normalize_crest(png((20, 20)))
        with self.assertRaises(CrestError):
            normalize_crest(png((100, 100), color=(0, 0, 0, 0)))

    def test_save_and_remove_custom_crest(self):
        path = save_custom_crest(100, png(fmt="PNG"), self.database_path, root=self.root)
        self.assertTrue(path.is_file())
        self.assertEqual(path.name, "100.png")
        with connect(self.database_path) as connection:
            connection.execute(
                "UPDATE game_teams SET logo_path = 'clubs/loja.png' WHERE discord_user_id = '100'"
            )
        team = get_team(100, self.database_path)
        self.assertEqual(team["custom_logo_path"], "escudos/100.png")
        self.assertEqual(team_logo_path(team), "escudos/100.png")
        self.assertTrue(remove_custom_crest(100, self.database_path, root=self.root))
        self.assertFalse(path.exists())
        team = get_team(100, self.database_path)
        self.assertEqual(team_logo_path(team), "clubs/loja.png")
        self.assertFalse(remove_custom_crest(100, self.database_path, root=self.root))

    def test_jpeg_is_accepted_and_team_must_exist(self):
        save_custom_crest(
            100,
            png(mode="RGB", color=(10, 90, 200), fmt="JPEG"),
            self.database_path,
            root=self.root,
        )
        with self.assertRaises(CrestError):
            save_custom_crest(999, png(), self.database_path, root=self.root)


class ScoreGifTests(unittest.TestCase):
    goals = [
        {"minute": 10, "side": "home", "scorer_name": "Fulano"},
        {"minute": 80, "side": "away", "scorer_name": "Beltrano", "play": "contra"},
    ]

    def render(self, title, penalties=None):
        data = render_score_gif(
            {"team_name": "Casa", "color": "#C0392B"},
            {"team_name": "Fora", "color": "#2E86C1"},
            1,
            1,
            self.goals,
            title=title,
            penalties=penalties,
        ).getvalue()
        return data, Image.open(BytesIO(data))

    def test_friendly_and_championship_gifs_are_animated_and_small(self):
        for title in ("Amistoso", "FINAL do campeonato"):
            with self.subTest(title=title):
                data, image = self.render(title, penalties=(4, 3))
                self.assertEqual(image.format, "GIF")
                self.assertGreater(image.n_frames, 20)
                self.assertLess(len(data), 8 * 1024 * 1024)

    def test_championship_gif_differs_from_friendly(self):
        friendly, _ = self.render("Amistoso")
        championship, _ = self.render("FINAL do campeonato")
        self.assertNotEqual(friendly, championship)


class FeedOrderTests(unittest.TestCase):
    def test_live_feed_runs_top_to_bottom(self):
        events = [
            {
                "minute": minute,
                "stoppage": 0,
                "kind": "post",
                "text": "",
                "side": "home",
                "home_goals": 0,
                "away_goals": 0,
                "player": f"Jogador {minute}",
            }
            for minute in (5, 20, 40)
        ]
        description = match_embed("A", "B", events).description
        self.assertLess(description.index("Jogador 5"), description.index("Jogador 40"))


if __name__ == "__main__":
    unittest.main()
