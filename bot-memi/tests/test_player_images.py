import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from PIL import Image

from src.db import connect, initialize_database
from src.game import top_player_cards
from src.player_images import (
    CARD_SIZE,
    GIF_SIZE,
    MAX_IMAGES,
    ImageError,
    add_image,
    delete_image,
    list_images,
    set_main,
)


def animated_gif(frames=6, size=(400, 300)):
    images = [Image.new("RGB", size, (40 * n, 80, 200 - 30 * n)) for n in range(frames)]
    buffer = BytesIO()
    images[0].save(buffer, format="GIF", save_all=True, append_images=images[1:], duration=90)
    return buffer.getvalue()


def picture(size=(300, 400), color=(200, 40, 40)):
    buffer = BytesIO()
    Image.new("RGB", size, color).save(buffer, format="PNG")
    return buffer.getvalue()


class PlayerImageTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name) / "photos"
        (self.root / "players").mkdir(parents=True)
        self.database_path = Path(self.tempdir.name) / "img.sqlite"
        initialize_database(self.database_path)
        with connect(self.database_path) as connection:
            self.player = connection.execute(
                "INSERT INTO players (name, game_position, overall, activity_status, "
                "phase2_candidate) VALUES ('Eusébio', 'CA', 97, 'active', 0)"
            ).lastrowid

    def tearDown(self):
        self.tempdir.cleanup()

    def photo_path(self):
        with connect(self.database_path) as connection:
            return connection.execute(
                "SELECT photo_path FROM players WHERE id = ?", (self.player,)
            ).fetchone()[0]

    def test_first_image_of_a_player_without_photo_becomes_main(self):
        first = add_image(self.player, picture(), 100, self.database_path, root=self.root)
        self.assertTrue(first["main"])
        self.assertEqual(self.photo_path(), first["image_path"])
        second = add_image(self.player, picture(), 100, self.database_path, root=self.root)
        self.assertFalse(second["main"])  # já tem foto: a principal não muda sozinha
        self.assertEqual(self.photo_path(), first["image_path"])
        images = list_images(self.player, self.database_path, root=self.root)
        self.assertEqual([image["main"] for image in images], [True, False])

    def test_add_keeps_original_and_choose_main(self):
        original = self.root / "players" / f"{self.player}.png"
        original.write_bytes(picture(color=(0, 0, 200)))
        with connect(self.database_path) as connection:
            connection.execute(
                "UPDATE players SET photo_path = ? WHERE id = ?",
                (str(original), self.player),
            )
        added = add_image(self.player, picture(), 100, self.database_path, root=self.root)
        images = list_images(self.player, self.database_path, root=self.root)
        self.assertEqual([image["source"] for image in images], ["original", "custom"])
        self.assertTrue((self.root / added["image_path"]).is_file())
        self.assertEqual(self.photo_path(), str(original))  # cadastrar não troca a principal
        set_main(self.player, added["id"], self.database_path)
        self.assertEqual(self.photo_path(), added["image_path"])
        images = list_images(self.player, self.database_path, root=self.root)
        self.assertEqual([image["main"] for image in images], [False, True])

    def test_deleting_the_main_image_goes_back_to_the_original(self):
        original = self.root / "players" / f"{self.player}.png"
        original.write_bytes(picture())
        added = add_image(self.player, picture(), 100, self.database_path, root=self.root)
        set_main(self.player, added["id"], self.database_path)
        delete_image(self.player, added["id"], self.database_path, root=self.root)
        self.assertFalse((self.root / added["image_path"]).exists())
        self.assertEqual(self.photo_path(), f"players/{self.player}.png")
        original_id = list_images(self.player, self.database_path, root=self.root)[0]["id"]
        with self.assertRaises(ImageError):
            delete_image(self.player, original_id, self.database_path, root=self.root)

    def test_rejects_bad_images_and_respects_the_limit(self):
        with self.assertRaises(ImageError):
            add_image(self.player, b"nada", 100, self.database_path, root=self.root)
        with self.assertRaises(ImageError):
            add_image(self.player, picture((40, 40)), 100, self.database_path, root=self.root)
        for _ in range(MAX_IMAGES):
            add_image(self.player, picture(), 100, self.database_path, root=self.root)
        with self.assertRaises(ImageError):
            add_image(self.player, picture(), 100, self.database_path, root=self.root)

    def test_every_image_gets_the_card_photo_shape(self):
        for size in ((3000, 1000), (300, 900), (640, 640)):
            added = add_image(self.player, picture(size), 100, self.database_path, root=self.root)
            with Image.open(self.root / added["image_path"]) as saved:
                self.assertEqual(saved.size, CARD_SIZE)

    def test_gif_stays_animated_in_card_shape(self):
        added = add_image(self.player, animated_gif(), 100, self.database_path, root=self.root)
        self.assertTrue(added["image_path"].endswith(".gif"))
        with Image.open(self.root / added["image_path"]) as saved:
            self.assertEqual(saved.size, GIF_SIZE)
            self.assertEqual(saved.n_frames, 6)


class GalleryAndTopTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.tempdir.name) / "gallery.sqlite"
        initialize_database(self.database_path)
        with connect(self.database_path) as connection:
            for name, overall in (("Fraco", 50), ("Eusébio", 97), ("Médio", 80)):
                connection.execute(
                    "INSERT INTO players (name, game_position, overall, overall_source, "
                    "activity_status, phase2_candidate) VALUES (?, 'CA', ?, ?, 'active', 0)",
                    (name, overall, "Lenda" if name == "Eusébio" else None),
                )

    def tearDown(self):
        self.tempdir.cleanup()

    async def test_topi_lists_cards_by_overall(self):
        from src.cogs.common import CardPagerView
        from src.cogs.game import top_line

        players = top_player_cards(self.database_path)
        self.assertEqual([p["name"] for p in players], ["Eusébio", "Médio", "Fraco"])
        view = CardPagerView(players, title="TOP jogadores", line_format=top_line)
        self.assertIn("Eusébio", view.lines()[0])
        self.assertIn("1/3", view.page().footer.text)

    async def test_gallery_buttons_follow_permissions(self):
        from src.cogs.game import ImageGalleryView

        player = top_player_cards(self.database_path)[0]
        viewer = ImageGalleryView(player, 1, False, self.database_path)
        self.assertTrue(viewer.make_main.disabled)
        self.assertIn("Só o dono", viewer.page().description)
        root = Path(self.tempdir.name)
        first = add_image(int(player["id"]), picture(), 1, self.database_path, root=root)
        self.assertTrue(first["main"])  # sem foto: a primeira já vira a principal
        added = add_image(int(player["id"]), picture(), 1, self.database_path, root=root)
        editor = ImageGalleryView(player, 1, True, self.database_path, start=added["id"])
        self.assertFalse(editor.make_main.disabled)
        self.assertFalse(editor.delete.disabled)
        interaction = SimpleNamespace(
            response=SimpleNamespace(defer=AsyncMock()), edit_original_response=AsyncMock()
        )
        await editor.make_main.callback(interaction)
        self.assertTrue(editor.current()["main"])
        self.assertTrue(editor.make_main.disabled)

    async def test_gif_as_main_makes_an_animated_card(self):
        from src.cogs.common import card_embed, card_file
        from src.roll_images import PHOTO_ROOT

        player = top_player_cards(self.database_path)[0]
        added = add_image(int(player["id"]), animated_gif(), 1, self.database_path)
        self.addCleanup((PHOTO_ROOT / added["image_path"]).unlink, missing_ok=True)
        set_main(int(player["id"]), added["id"], self.database_path)
        player = top_player_cards(self.database_path)[0]
        file = await card_file(player)
        self.assertEqual(file.filename, "carta.gif")
        self.assertEqual(card_embed(player).image.url, "attachment://carta.gif")
        with Image.open(file.fp) as card:
            self.assertGreater(card.n_frames, 1)


if __name__ == "__main__":
    unittest.main()
