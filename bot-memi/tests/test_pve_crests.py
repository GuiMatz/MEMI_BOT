import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from src import pve_crests
from src.db import initialize_database
from src.pve import legend_teams
from src.roll_images import find_image
from src.visual import contain


class PveCrestTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        root = Path(self.tempdir.name)
        self.photos = root / "photos"
        self.database_path = root / "c.sqlite"
        initialize_database(self.database_path)
        # Sem os escudos do repositório, para testar a busca e o cache.
        self.patches = [
            patch.object(pve_crests, "PHOTO_ROOT", self.photos),
            patch.object(pve_crests, "BUNDLED", root / "sem-escudos"),
        ]
        for item in self.patches:
            item.start()
        pve_crests._failed.clear()

    def tearDown(self):
        for item in self.patches:
            item.stop()
        self.tempdir.cleanup()

    def test_club_crest_takes_the_exact_brazilian_club(self):
        teams = [
            {
                "strTeam": "Santos Laguna",
                "strSport": "Soccer",
                "strCountry": "Mexico",
                "strBadge": "https://x/laguna.png",
            },
            {
                "strTeam": "Santos",
                "strSport": "Soccer",
                "strCountry": "Mexico",
                "strBadge": "https://x/outro.png",
            },
            {
                "strTeam": "Santos",
                "strSport": "Soccer",
                "strCountry": "Brazil",
                "strGender": "Male",
                "strBadge": "https://x/santos.png",
            },
        ]

        class FakeSportsDB:
            def __init__(self, *args, **kwargs):
                pass

            def get(self, endpoint, **params):
                return {"teams": teams}

        with patch("src.sportsdb_images.SportsDB", FakeSportsDB):
            self.assertEqual(pve_crests._sportsdb_badge("Santos", "Brazil"), "https://x/santos.png")
            self.assertIsNone(pve_crests._sportsdb_badge("Santos Futebol", "Brazil"))

    def test_club_crest_is_downloaded_once_into_its_folder(self):
        def fake_download(collector, url, entity, entity_id):
            path = Path(entity) / f"{entity_id}.png"
            (self.photos / path).parent.mkdir(parents=True, exist_ok=True)
            (self.photos / path).write_bytes(b"png")
            return path

        with (
            patch.object(pve_crests, "_sportsdb_badge", return_value="https://x/cam.png") as badge,
            patch("src.photos.PhotoCollector._download", fake_download),
        ):
            first = pve_crests.crest_for("clube:atlético-mg:2026-10-23", self.database_path)
            again = pve_crests.crest_for("clube:atlético-mg", self.database_path)
        self.assertEqual(first, "lendas_clubes/atletico-mg.png")
        self.assertEqual(again, first)
        self.assertEqual(badge.call_count, 1)
        self.assertEqual(badge.call_args.args, ("Atletico Mineiro", "Brazil"))

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


class BundledCrestTests(unittest.TestCase):
    def test_every_legend_team_has_its_crest_in_the_repo_without_internet(self):
        with (
            tempfile.TemporaryDirectory() as folder,
            patch.object(pve_crests, "PHOTO_ROOT", Path(folder)),
            patch.object(pve_crests, "_sportsdb_badge", side_effect=AssertionError("rede")),
        ):
            for team in legend_teams():
                path = pve_crests.crest_for(team.key)
                self.assertTrue(path and path.startswith("assets/escudos/"), team.key)
                self.assertIsNotNone(find_image(path), team.key)


class ContainTests(unittest.TestCase):
    def test_small_crest_with_empty_border_fills_the_space(self):
        picture = Image.new("RGBA", (100, 100), (0, 0, 0, 0))
        picture.paste(Image.new("RGBA", (20, 30), (200, 0, 0, 255)), (40, 35))
        fitted = contain(picture, (210, 210))
        self.assertEqual(fitted.size, (210, 210))
        self.assertEqual(fitted.getchannel("A").getbbox()[1::2], (0, 210))  # altura toda


if __name__ == "__main__":
    unittest.main()
