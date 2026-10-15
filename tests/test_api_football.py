import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock

from src.api_football import (
    ApiFootballClient,
    import_league_players,
)
from src.db import connect, initialize_database


class ApiFootballClientTests(unittest.TestCase):
    def test_requires_api_key(self):
        with self.assertRaisesRegex(ValueError, "API_FOOTBALL_KEY"):
            ApiFootballClient(api_key="")

    def test_reads_and_validates_remaining_header(self):
        session = Mock()
        response = Mock()
        response.headers = {"x-ratelimit-requests-remaining": "12"}
        response.status_code = 200
        response.json.return_value = {
            "response": [],
            "paging": {"current": 1, "total": 1},
            "errors": {},
        }
        session.get.return_value = response
        client = ApiFootballClient("not-a-real-key", session)

        players, pages = client.players_page(39, 2024, 1)

        self.assertEqual((players, pages, client.remaining_requests), ([], 1, 12))
        self.assertEqual(
            session.get.call_args.kwargs["params"],
            {"league": 39, "season": 2024, "page": 1},
        )
        self.assertEqual(session.get.call_args.kwargs["timeout"], (5, 30))

    def test_missing_quota_header_stops(self):
        session = Mock()
        response = Mock()
        response.headers = {}
        response.status_code = 200
        session.get.return_value = response
        client = ApiFootballClient("not-a-real-key", session)
        with self.assertRaisesRegex(RuntimeError, "x-ratelimit-requests-remaining"):
            client.players_page(39, 2024, 1)

    def test_exhausted_quota_prevents_next_request(self):
        session = Mock()
        client = ApiFootballClient("not-a-real-key", session)
        client.remaining_requests = 0
        with self.assertRaisesRegex(RuntimeError, "esgotada"):
            client.players_page(39, 2024, 1)
        session.get.assert_not_called()

    def test_api_errors_are_reported(self):
        session = Mock()
        response = Mock()
        response.headers = {"x-ratelimit-requests-remaining": "99"}
        response.status_code = 200
        response.json.return_value = {
            "response": [],
            "paging": {"current": 1, "total": 1},
            "errors": {"season": "Season not available"},
        }
        session.get.return_value = response
        client = ApiFootballClient("not-a-real-key", session)
        with self.assertRaisesRegex(RuntimeError, "Season not available"):
            client.players_page(39, 1990, 1)


class ApiFootballImportTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary_directory.name) / "memi.sqlite"

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_import_is_resumable_and_retains_source_position(self):
        class FakeClient:
            remaining_requests = 98

            def __init__(self):
                self.calls = []

            def players_page(self, league_id, season, page):
                self.calls.append(page)
                entries = [
                    {
                        "player": {
                            "id": 1,
                            "name": "Jogador API",
                            "birth": {"date": "2000-01-01"},
                            "nationality": "Brazil",
                            "photo": "https://example.test/player.png",
                        },
                        "statistics": [
                            {
                                "team": {
                                    "id": 10,
                                    "name": "Time API",
                                    "logo": "https://example.test/team.png",
                                },
                                "games": {"position": "Attacker"},
                            }
                        ],
                    },
                    {
                        "player": {"id": 2, "name": "Outro Jogador"},
                        "statistics": [],
                    },
                ]
                if page == 1:
                    return entries, 2
                return [
                    {
                        "player": {"id": 3, "name": "Retomado"},
                        "statistics": [],
                    }
                ], 2

        client = FakeClient()
        first = import_league_players(client, 39, 2024, self.database_path, limit=1)
        self.assertEqual(first["players"], 1)
        with connect(self.database_path) as connection:
            checkpoint = json.loads(
                connection.execute(
                    "SELECT checkpoint FROM import_checkpoints"
                ).fetchone()["checkpoint"]
            )
            player = connection.execute(
                "SELECT * FROM players WHERE name = ?", ("Jogador API",)
            ).fetchone()
            source = connection.execute(
                "SELECT * FROM player_sources WHERE source = 'api_football'"
            ).fetchone()
            self.assertEqual(checkpoint, {"page": 1, "offset": 1})
            self.assertEqual(player["game_position"], None)
            self.assertEqual(player["original_position"], "Attacker")
            self.assertEqual(source["source_club_name"], "Time API")

        second = import_league_players(client, 39, 2024, self.database_path, limit=1)
        self.assertEqual(second["players"], 1)
        third = import_league_players(client, 39, 2024, self.database_path, limit=1)
        self.assertEqual(third["players"], 1)
        self.assertEqual(client.calls, [1, 1, 2])
        with connect(self.database_path) as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM player_sources WHERE source = 'api_football'"
                ).fetchone()[0],
                3,
            )
            checkpoint = json.loads(
                connection.execute(
                    "SELECT checkpoint FROM import_checkpoints"
                ).fetchone()["checkpoint"]
            )
            self.assertEqual(checkpoint, {"page": 3, "offset": 0})

    def test_saved_zero_daily_quota_prevents_api_call(self):
        class FakeClient:
            remaining_requests = 7
            quota_date = None

            def players_page(self, league_id, season, page):
                raise AssertionError("A API não deve ser chamada com saldo salvo zero.")

        initialize_database(self.database_path)
        with connect(self.database_path) as connection:
            connection.execute(
                """
                INSERT INTO import_checkpoints (source, checkpoint)
                VALUES (?, ?)
                """,
                (
                    "api_football:quota",
                    json.dumps(
                        {
                            "date": datetime.now(timezone.utc).date().isoformat(),
                            "remaining": 0,
                        }
                    ),
                ),
            )
        with self.assertRaisesRegex(RuntimeError, "esgotada"):
            import_league_players(FakeClient(), 39, 2024, self.database_path)


if __name__ == "__main__":
    unittest.main()
