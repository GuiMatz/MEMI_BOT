import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src import pve_crests
from src.db import connect, initialize_database


class PveCrestTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        root = Path(self.tempdir.name)
        self.photos = root / "photos"
        self.database_path = root / "c.sqlite"
        initialize_database(self.database_path)
        self.patch = patch.object(pve_crests, "PHOTO_ROOT", self.photos)
        self.patch.start()
        pve_crests._failed.clear()

    def tearDown(self):
        self.patch.stop()
        self.tempdir.cleanup()

    def club(self, name, country, file_name):
        (self.photos / "clubs").mkdir(parents=True, exist_ok=True)
        (self.photos / "clubs" / file_name).write_bytes(b"png")
        with connect(self.database_path) as connection:
            connection.execute(
                "INSERT INTO clubs (name, country, logo_path) VALUES (?, ?, ?)",
                (name, country, f"clubs/{file_name}"),
            )

    def test_club_crest_prefers_the_brazilian_club(self):
        self.club("Santos Laguna", "Mexico", "1.png")
        self.club("Santos FC", "Brasil", "2.png")
        self.club("CR Vasco da Gama", "Brasil", "3.png")
        self.club("Clube Atlético Mineiro", "Brazil", "4.png")
        self.assertEqual(pve_crests.club_crest("Santos", self.database_path), "clubs/2.png")
        self.assertEqual(
            pve_crests.crest_for("clube:vasco da gama", self.database_path), "clubs/3.png"
        )
        # Chefão do dia: a chave tem a data no fim.
        self.assertEqual(
            pve_crests.crest_for("clube:atlético-mg:2026-10-23", self.database_path),
            "clubs/4.png",
        )
        self.assertIsNone(pve_crests.club_crest("Flamengo", self.database_path))

    def test_national_crest_uses_the_saved_file_and_gives_up_quietly(self):
        (self.photos / "selecoes").mkdir(parents=True)
        (self.photos / "selecoes" / "italia.png").write_bytes(b"png")
        self.assertEqual(
            pve_crests.crest_for("selecao:itália:2026-W43", self.database_path),
            "selecoes/italia.png",
        )
        with patch.object(
            pve_crests, "_sportsdb_badge", side_effect=RuntimeError("sem rede")
        ) as badge:
            self.assertIsNone(pve_crests.national_crest("Brasil"))
            self.assertIsNone(pve_crests.national_crest("Brasil"))
        self.assertEqual(badge.call_count, 1)  # não tenta de novo enquanto o bot está ligado

    def test_world_crest_is_drawn_once(self):
        path = pve_crests.crest_for("mundial", self.database_path)
        self.assertEqual(path, "selecoes/selecao-mundial.png")
        self.assertTrue((self.photos / path).is_file())


if __name__ == "__main__":
    unittest.main()
