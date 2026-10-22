"""Testes do coletor de imagens do TheSportsDB (com um TheSportsDB falso)."""

import tempfile
import unittest
from pathlib import Path

from src.db import connect, initialize_database
from src.sportsdb_images import ImageHarvester, SportsDB, best_league

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64


class FakeResponse:
    def __init__(self, payload=None, content=None, status=200):
        self.payload = payload
        self.content = content
        self.status_code = status
        self.headers = {"Content-Type": "image/png"} if content is not None else {}

    def json(self):
        return self.payload

    def iter_content(self, chunk_size):
        yield self.content

    def close(self):
        pass


class FakeSession:
    """Responde como o TheSportsDB; imagens vêm de https://img/<nome>.png."""

    def __init__(self):
        self.calls = []

    def get(self, url, params=None, timeout=None, stream=False):
        self.calls.append((url.rsplit("/", 1)[-1], dict(params or {})))
        if url.startswith("https://img/"):
            return FakeResponse(content=PNG)
        endpoint = url.rsplit("/", 1)[-1]
        params = params or {}
        if endpoint == "search_all_leagues.php":
            if params.get("c") == "England":
                return FakeResponse(
                    {
                        "countries": [
                            {
                                "idLeague": "4328",
                                "strLeague": "English Premier League",
                                "strLeagueAlternate": "Premier League, EPL",
                                "strBadge": "https://img/epl.png",
                            },
                            {
                                "idLeague": "4329",
                                "strLeague": "English League Championship",
                                "strBadge": "https://img/champ.png",
                            },
                        ]
                    }
                )
            return FakeResponse({"countries": None})
        if endpoint == "searchplayers.php":
            people = {
                "Pep Guardiola": [
                    {
                        "strPlayer": "Pep Guardiola",
                        "strSport": "Soccer",
                        "strNationality": "Spain",
                        "strPosition": "Manager",
                        "strCutout": "https://img/pep.png",
                    },
                    {
                        "strPlayer": "Pep Guardiola",
                        "strSport": "Soccer",
                        "strNationality": "Spain",
                        "strPosition": "Midfielder",
                        "strCutout": "https://img/pep-old.png",
                    },
                ],
                "Ronaldo Fenômeno": [],
                "Ronaldo": [
                    {
                        "strPlayer": "Ronaldo",
                        "strSport": "Soccer",
                        "strNationality": "Brazil",
                        "strStatus": "Retired",
                        "strThumb": "https://img/r9.png",
                    },
                    {
                        "strPlayer": "Ronaldo",
                        "strSport": "Soccer",
                        "strNationality": "Portugal",
                        "strThumb": "https://img/outro.png",
                    },
                ],
            }
            return FakeResponse({"player": people.get(params.get("p"))})
        if endpoint == "searchvenues.php":
            if params.get("v") == "Maracanã":
                # A busca curta só acha o ginásio ao lado: não pode ser aceito.
                return FakeResponse(
                    {"venues": [{"strVenue": "Maracanãzinho", "strThumb": "https://img/zinho.png"}]}
                )
            if params.get("v") == "Estadio do Maracanã":
                return FakeResponse(
                    {
                        "venues": [
                            {"strVenue": "Estádio do Maracanã", "strThumb": "https://img/mara.png"}
                        ]
                    }
                )
            return FakeResponse({"venues": None})
        if endpoint in ("search_all_teams.php", "lookup_all_teams.php"):
            return FakeResponse(
                {
                    "teams": [
                        {
                            "strTeam": "Arsenal",
                            "strSport": "Soccer",
                            "strCountry": "England",
                            "strBadge": "https://img/ars.png",
                        },
                        {
                            "strTeam": "Chelsea",
                            "strSport": "Soccer",
                            "strCountry": "England",
                            "strBadge": "https://img/che.png",
                        },
                        {
                            "strTeam": "Liverpool",
                            "strTeamAlternate": "Liverpool FC",
                            "strSport": "Soccer",
                            "strCountry": "England",
                            "strBadge": "https://img/liv.png",
                        },
                    ]
                }
            )
        return FakeResponse({})


class HarvesterTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        root = Path(self.tempdir.name)
        self.database_path = root / "game.sqlite"
        self.photos = root / "photos"
        initialize_database(self.database_path)
        with connect(self.database_path) as connection:
            connection.execute(
                "INSERT INTO leagues (id, name, country) VALUES (1, 'Premier League', 'Inglaterra')"
            )
            connection.execute(
                "INSERT INTO leagues (id, name, country) VALUES (2, 'Liga Inventada', 'Brasil')"
            )
            connection.execute(
                "INSERT INTO coaches (id, name, nationality) VALUES (7, 'Pep Guardiola', 'Espanha')"
            )
            connection.execute("INSERT INTO stadiums (id, name) VALUES (3, 'Maracanã')")
            connection.execute(
                "INSERT INTO players (id, name, nationality, game_position, overall, overall_source) "
                "VALUES (50, 'Ronaldo Fenômeno', 'Brazil', 'CA', 98, 'Lenda')"
            )
            connection.execute("INSERT INTO clubs (id, name) VALUES (9, 'Arsenal FC')")
            connection.execute(
                "INSERT INTO clubs (id, name, logo_path) VALUES (10, 'Chelsea', 'data/assets/x.png')"
            )
        self.session = FakeSession()
        self.harvester = ImageHarvester(
            self.database_path,
            SportsDB("123", self.session, interval=0),
            self.photos,
            league_index=[],  # sem índice: usa a lista por país
        )

    def tearDown(self):
        self.tempdir.cleanup()

    def value(self, sql):
        with connect(self.database_path) as connection:
            return connection.execute(sql).fetchone()[0]

    def test_league_badge_is_saved_with_the_league_id(self):
        report = self.harvester.collect_leagues()
        self.assertEqual(report, {"baixadas": 1, "nao_encontradas": 1})
        self.assertEqual(self.value("SELECT logo_path FROM leagues WHERE id = 1"), "leagues/1.png")
        self.assertTrue((self.photos / "leagues" / "1.png").is_file())

    def test_incomplete_index_falls_back_to_country_list(self):
        self.harvester.league_index = [
            {"id": 4351, "name": "Brazilian Serie A", "alternates": "", "country": "Brazil"}
        ]
        found = self.harvester.match_league("Premier League", "Inglaterra")
        self.assertEqual(found["name"], "English Premier League")

    def test_coach_prefers_the_manager_record(self):
        self.harvester.collect_coaches()
        self.assertEqual(
            self.value("SELECT photo_url FROM coaches WHERE id = 7"), "https://img/pep.png"
        )
        self.assertEqual(self.value("SELECT photo_path FROM coaches WHERE id = 7"), "coaches/7.png")

    def test_stadium_tries_the_official_name_and_skips_lookalikes(self):
        self.harvester.collect_stadiums()
        self.assertEqual(
            self.value("SELECT photo_url FROM stadiums WHERE id = 3"), "https://img/mara.png"
        )
        self.assertEqual(
            self.value("SELECT photo_path FROM stadiums WHERE id = 3"), "stadiums/3.png"
        )

    def test_legend_uses_nickname_and_nationality(self):
        self.harvester.collect_legends()
        self.assertEqual(
            self.value("SELECT photo_url FROM players WHERE id = 50"), "https://img/r9.png"
        )
        self.assertEqual(
            self.value("SELECT photo_path FROM players WHERE id = 50"), "players/50.png"
        )

    def test_teams_fill_missing_badges_and_never_duplicate(self):
        report = self.harvester.collect_teams()
        again = self.harvester.collect_teams()
        self.assertEqual(report["escudos_novos"], 1)  # Arsenal FC já existia, sem escudo
        self.assertEqual(report["times_criados"], 1)  # Liverpool é novo
        self.assertEqual(report["ja_tinham"], 1)  # Chelsea já tinha escudo
        self.assertEqual(again["times_criados"], 0)
        self.assertEqual(self.value("SELECT COUNT(*) FROM clubs WHERE name LIKE 'Arsenal%'"), 1)
        self.assertEqual(self.value("SELECT COUNT(*) FROM clubs WHERE name = 'Liverpool'"), 1)
        self.assertEqual(self.value("SELECT logo_path FROM clubs WHERE id = 9"), "clubs/9.png")

    def test_not_found_is_not_searched_again_unless_retrying(self):
        self.harvester.collect_leagues()
        calls = len(self.session.calls)
        self.harvester.collect_leagues()
        self.assertEqual(len(self.session.calls), calls)  # 'Liga Inventada' não é buscada de novo
        self.harvester.retry = True
        self.harvester._leagues_by_country.clear()
        self.harvester.collect_leagues()
        self.assertGreater(len(self.session.calls), calls)

    def test_new_badges_become_emblems(self):
        from src.cosmetics import catalog

        self.harvester.collect_teams()
        names = {item["name"] for item in catalog("emblema", self.database_path)}
        self.assertIn("Liverpool", names)


INDEX = [
    {
        "id": 4351,
        "name": "Brazilian Serie A",
        "alternates": "Brazil Brasileirão Série A Brasileiro",
        "country": "Brazil",
    },
    {
        "id": 4404,
        "name": "Brazilian Serie B",
        "alternates": "Brasileirão Série B",
        "country": "Brazil",
    },
    {
        "id": 4625,
        "name": "Brazil Brasileiro Women",
        "alternates": "Série A1, Campeonato Brasileiro Feminino Série A1",
        "country": "Brazil",
    },
    {
        "id": 4627,
        "name": "Chilean Primera Division",
        "alternates": "Campeonato Nacional",
        "country": "Chile",
    },
    {"id": 4332, "name": "Italian Serie A", "alternates": "Italy Serie A", "country": "Italy"},
    {
        "id": 4810,
        "name": "Italian Serie A Women",
        "alternates": "Serie A Femminile",
        "country": "Italy",
    },
    {
        "id": 4331,
        "name": "German Bundesliga",
        "alternates": "Bundesliga, Fußball-Bundesliga",
        "country": "Germany",
    },
    {
        "id": 4399,
        "name": "German 2. Bundesliga",
        "alternates": "2. Bundesliga",
        "country": "Germany",
    },
]


class LeagueIndexTests(unittest.TestCase):
    def find(self, name, country):
        from src.sportsdb_images import _country_names

        names = _country_names(country)
        options = [item for item in INDEX if item["country"] in names]
        found = best_league(name, names, options)
        return found["id"] if found else None

    def test_portuguese_names_match_english_leagues(self):
        self.assertEqual(self.find("Campeonato Brasileiro Série A", "Brasil"), 4351)
        self.assertEqual(self.find("Campeonato Brasileiro Série B", "Brasil"), 4404)
        self.assertEqual(self.find("Brasileirão Feminino", "Brasil"), 4625)
        self.assertEqual(self.find("Primera División Chilena", "Chile"), 4627)

    def test_closest_league_wins_and_unknown_is_none(self):
        self.assertEqual(self.find("Serie A", "Itália"), 4332)
        self.assertEqual(self.find("Serie A Femminile", "Itália"), 4810)
        self.assertEqual(self.find("Bundesliga", "Alemanha"), 4331)
        self.assertEqual(self.find("2. Bundesliga", "Alemanha"), 4399)
        self.assertIsNone(self.find("Liga Inventada", "Brasil"))


if __name__ == "__main__":
    unittest.main()
