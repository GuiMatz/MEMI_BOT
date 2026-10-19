import tempfile
import unittest
from pathlib import Path

from PIL import Image

from src.db import ROOT
from src.roll_images import PLACEHOLDER_FILENAME, roll_image_file


class RollImageTests(unittest.TestCase):
    def test_missing_photo_uses_local_silhouette(self):
        image_file = roll_image_file({})

        self.assertEqual(image_file.filename, PLACEHOLDER_FILENAME)
        image_file.fp.seek(0)
        with Image.open(image_file.fp) as image:
            self.assertEqual(image.format, "PNG")
            self.assertEqual(image.size, (360, 360))

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
