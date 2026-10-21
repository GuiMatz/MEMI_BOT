import tempfile
import unittest
from pathlib import Path

from PIL import Image

from src.db import ROOT
from src.roll_images import PLACEHOLDER_PATH, roll_image_file


class RollImageTests(unittest.TestCase):
    def test_missing_photo_uses_local_silhouette(self):
        image_file = roll_image_file({})

        try:
            expected = (
                ("silhueta.jpg", "JPEG") if PLACEHOLDER_PATH.is_file() else ("silhueta.png", "PNG")
            )
            self.assertEqual(image_file.filename, expected[0])
            image_file.fp.seek(0)
            with Image.open(image_file.fp) as image:
                self.assertEqual(image.format, expected[1])
        finally:
            image_file.close()

    def test_existing_photo_is_attached(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary_directory:
            photo_path = Path(temporary_directory) / "player.png"
            Image.new("RGB", (12, 12), "blue").save(photo_path)

            image_file = roll_image_file({"photo_path": str(photo_path)})

            try:
                self.assertEqual(image_file.filename, "jogador.png")
                image_file.fp.seek(0)
                self.assertEqual(image_file.fp.read(8), b"\x89PNG\r\n\x1a\n")
            finally:
                image_file.close()

    def test_worker_relative_photo_path_is_attached(self):
        photo_path = ROOT / "data" / "photos" / "players" / "test-roll.png"
        photo_path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (12, 12), "blue").save(photo_path)
        try:
            image_file = roll_image_file({"photo_path": "players/test-roll.png"})
            self.assertEqual(image_file.filename, "jogador.png")
            image_file.close()
        finally:
            photo_path.unlink(missing_ok=True)


class FindImageTests(unittest.TestCase):
    def test_downloaded_photo_is_used_even_without_database_path(self):
        photo_path = ROOT / "data" / "photos" / "players" / "987654321.png"
        photo_path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (12, 12), "red").save(photo_path)
        try:
            image_file = roll_image_file({"id": 987654321, "photo_path": None})
            self.assertEqual(image_file.filename, "jogador.png")
            image_file.close()
        finally:
            photo_path.unlink(missing_ok=True)
