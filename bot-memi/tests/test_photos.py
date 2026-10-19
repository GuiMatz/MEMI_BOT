import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from src.db import connect, initialize_database
from src.photos import PhotoCollector, collect_photos


class PhotoCollectorTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.database_path = self.root / "memi.sqlite"
        self.photo_root = self.root / "photos"
        initialize_database(self.database_path)

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_downloads_known_url_and_stores_relative_path(self):
        session = Mock()
        response = Mock()
        response.status_code = 200
        response.headers = {"Content-Type": "image/png", "Content-Length": "4"}
        response.iter_content.return_value = [b"PNG!"]
        session.get.return_value = response
        with connect(self.database_path) as connection:
            connection.execute(
                "INSERT INTO players (name, photo_url) VALUES (?, ?)",
                ("Jogador", "https://example.test/player.png"),
            )

        result = collect_photos(self.database_path, self.photo_root, session=session)

        self.assertEqual(result["players"]["downloaded"], 1)
        with connect(self.database_path) as connection:
            row = connection.execute("SELECT photo_path FROM players").fetchone()
        self.assertEqual(row["photo_path"], "players/1.png")
        self.assertEqual((self.photo_root / "players" / "1.png").read_bytes(), b"PNG!")

    def test_missing_image_is_skipped(self):
        session = Mock()
        response = Mock()
        response.status_code = 404
        response.headers = {}
        session.get.return_value = response
        with connect(self.database_path) as connection:
            connection.execute(
                "INSERT INTO clubs (name, logo_url) VALUES (?, ?)",
                ("Clube", "https://example.test/missing.png"),
            )
        collector = PhotoCollector(self.photo_root, session)
        with connect(self.database_path) as connection:
            result = collector.collect_entity(connection, "clubs", False, None)
        self.assertEqual(result["missing"], 1)

    def test_the_sportsdb_is_used_when_source_image_is_missing(self):
        missing = Mock(status_code=404, headers={})
        search = Mock(
            status_code=200,
            json=lambda: {
                "player": [
                    {
                        "strPlayer": "Jogador",
                        "strCutout": "https://example.test/fallback.png",
                    }
                ]
            },
        )
        image = Mock(status_code=200)
        image.headers = {"Content-Type": "image/png", "Content-Length": "4"}
        image.iter_content.return_value = [b"PNG!"]
        session = Mock()
        session.get.side_effect = [missing, search, image]
        with connect(self.database_path) as connection:
            connection.execute(
                "INSERT INTO players (name, photo_url) VALUES (?, ?)",
                ("Jogador", "https://example.test/missing.png"),
            )

        result = collect_photos(
            self.database_path,
            self.photo_root,
            use_thesportsdb=True,
            session=session,
            thesportsdb_key="test-key",
        )

        self.assertEqual(result["players"]["downloaded"], 1)
        self.assertEqual(session.get.call_count, 3)

    def test_the_sports_db_requires_exact_name(self):
        session = Mock()
        response = Mock()
        response.status_code = 200
        response.json.return_value = {
            "teams": [
                {
                    "strTeam": "Clube Teste B",
                    "strTeamBadge": "https://example.test/wrong.png",
                },
                {
                    "strTeam": "Clube Teste",
                    "strTeamBadge": "https://example.test/right.png",
                },
            ]
        }
        session.get.return_value = response
        collector = PhotoCollector(self.photo_root, session, thesportsdb_key="test-key")
        self.assertEqual(
            collector._sportsdb_image_url("clubs", "Clube Teste"),
            "https://example.test/right.png",
        )

    def test_rejects_http_image_url(self):
        collector = PhotoCollector(self.photo_root, Mock())
        with self.assertRaisesRegex(ValueError, "URL de imagem inválida"):
            collector._download("http://example.test/image.png", "players", 1)

    def test_the_sportsdb_fallback_fails_explicitly_without_key(self):
        with connect(self.database_path) as connection:
            connection.execute("INSERT INTO players (name) VALUES (?)", ("Sem foto",))
        with self.assertRaisesRegex(ValueError, "THESPORTSDB_API_KEY"):
            collect_photos(
                self.database_path,
                self.photo_root,
                use_thesportsdb=True,
                session=Mock(),
                thesportsdb_key="",
            )


if __name__ == "__main__":
    unittest.main()
