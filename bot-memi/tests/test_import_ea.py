import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

import pandas as pd

from src.db import connect, initialize_database
from src.import_ea import POSITION_MAP, import_ea, simulate_ea_import


class EAImportTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.fc25_path = self.root / "ea_fc25.csv"
        self.database_path = self.root / "memi.sqlite"
        self._write_fc25()

    def tearDown(self):
        self.temporary_directory.cleanup()

    def _write_fc25(self, positions=None):
        positions = positions or ["ST, LW", "GK"]
        pd.DataFrame(
            [
                {
                    "player_id": "ea-1",
                    "long_name": "Atacante Teste",
                    "player_positions": positions[0],
                    "overall": 87,
                    "dob": "2001-03-04",
                    "nationality_name": "Brazil",
                    "club_name": "Clube EA",
                    "player_face_url": "https://example.test/ea-1.jpg",
                },
                {
                    "player_id": "ea-2",
                    "long_name": "Goleiro Teste",
                    "player_positions": positions[1],
                    "overall": 79,
                    "dob": None,
                    "nationality_name": "Portugal",
                    "club_name": "Outro Clube",
                    "player_face_url": None,
                },
            ]
        ).to_csv(self.fc25_path, index=False)

    def test_import_maps_primary_position_and_preserves_source_details(self):
        output = StringIO()
        with redirect_stdout(output):
            result = import_ea(
                [self.fc25_path],
                database_path=self.database_path,
                limit=1,
            )
        self.assertIn("ST, LW", output.getvalue())
        self.assertEqual(result["ea_fc25.csv"]["players"], 1)

        with connect(self.database_path) as connection:
            player = connection.execute("SELECT * FROM players").fetchone()
            source = connection.execute("SELECT * FROM player_sources").fetchone()
            self.assertEqual(player["game_position"], "CA")
            self.assertEqual(player["overall"], 87)
            self.assertEqual(player["overall_source"], "EA")
            self.assertEqual(player["overall_edition"], "FC25")
            self.assertEqual(player["activity_status"], "unknown")
            self.assertEqual(player["club_name"], "Clube EA")
            self.assertEqual(source["source_club_name"], "Clube EA")
            self.assertEqual(source["source_position"], "ST, LW")

    def test_import_is_idempotent_for_same_source_and_id(self):
        review_path = self.root / "review.csv"
        import_ea(
            [self.fc25_path],
            self.database_path,
            limit=1,
            show_positions=False,
            review_path=review_path,
        )
        import_ea(
            [self.fc25_path],
            self.database_path,
            limit=1,
            show_positions=False,
            review_path=review_path,
        )
        with connect(self.database_path) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM players").fetchone()[0], 1)
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM player_sources").fetchone()[0],
                1,
            )

    def test_same_player_from_different_editions_is_merged(self):
        fc24_path = self.root / "ea_fc24.csv"
        frame = pd.read_csv(self.fc25_path)
        frame.to_csv(fc24_path, index=False)
        import_ea([self.fc25_path, fc24_path], self.database_path, limit=1)
        with connect(self.database_path) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM players").fetchone()[0], 1)
            self.assertEqual(
                connection.execute("SELECT COUNT(DISTINCT source) FROM player_sources").fetchone()[
                    0
                ],
                2,
            )

    def test_unknown_position_stops_before_database_creation(self):
        self._write_fc25(["Unreviewed, ST", "GK"])
        with self.assertRaisesRegex(ValueError, "UNREVIEWED"):
            import_ea(
                [self.fc25_path],
                database_path=self.database_path,
                limit=1,
                show_positions=False,
            )
        self.assertFalse(self.database_path.exists())

    def test_overall_100_is_accepted(self):
        frame = pd.read_csv(self.fc25_path)
        frame.loc[0, "overall"] = 100
        frame.to_csv(self.fc25_path, index=False)
        import_ea(
            [self.fc25_path],
            database_path=self.database_path,
            limit=1,
            show_positions=False,
        )
        with connect(self.database_path) as connection:
            self.assertEqual(connection.execute("SELECT overall FROM players").fetchone()[0], 100)

    def test_non_integer_overall_is_rejected(self):
        frame = pd.read_csv(self.fc25_path)
        frame["overall"] = frame["overall"].astype(float)
        frame.loc[0, "overall"] = 87.5
        frame.to_csv(self.fc25_path, index=False)
        with self.assertRaisesRegex(ValueError, "não inteiro"):
            import_ea(
                [self.fc25_path],
                database_path=self.database_path,
                limit=1,
                show_positions=False,
            )

    def test_position_map_matches_requested_game_positions(self):
        self.assertEqual(POSITION_MAP["ST"], "CA")
        self.assertEqual(POSITION_MAP["CAM"], "MEI")
        self.assertEqual(POSITION_MAP["CDM"], "MC")
        self.assertEqual(POSITION_MAP["LWB"], "LE")
        self.assertEqual(POSITION_MAP["RWB"], "LD")
        self.assertEqual(POSITION_MAP["GK"], "GOL")

    def test_fc27_schema_uses_primary_position_and_falls_back_to_full_name(self):
        path = self.root / "players.csv"
        pd.DataFrame(
            [
                {
                    "player_id": 252371,
                    "common_name": None,
                    "first_name": "Jude",
                    "last_name": "Bellingham",
                    "position": "CAM",
                    "alternate_positions": "CM ST",
                    "overall_rating": 90,
                    "edition": "fc27",
                    "snapshot_date": "2026-09-12",
                    "age": 23,
                    "nationality": "England",
                    "club_name": "Real Madrid",
                }
            ]
        ).to_csv(path, index=False)
        result = import_ea([path], self.database_path, show_positions=False)
        self.assertEqual(result["players.csv"]["players"], 1)
        with connect(self.database_path) as connection:
            player = connection.execute("SELECT * FROM players").fetchone()
            source = connection.execute("SELECT * FROM player_sources").fetchone()
        self.assertEqual(player["name"], "Jude Bellingham")
        self.assertEqual(player["game_position"], "MEI")
        self.assertEqual(player["overall_source"], "EA")
        self.assertEqual(player["overall_edition"], "FC27")
        self.assertEqual(player["club_name"], "Real Madrid")
        self.assertEqual(source["source_club_name"], "Real Madrid")

    def test_fc26_edition_is_detected_from_file_name(self):
        path = self.root / "FC26_20250921.csv"
        frame = pd.read_csv(self.fc25_path)
        frame.to_csv(path, index=False)
        import_ea([path], self.database_path, limit=1, show_positions=False)
        with connect(self.database_path) as connection:
            player = connection.execute("SELECT * FROM players").fetchone()
        self.assertEqual(player["overall_edition"], "FC26")
        self.assertEqual(player["overall_source"], "EA")

    def test_latest_edition_wins_regardless_of_input_order(self):
        fc27_path = self.root / "FC27_20260912.csv"
        frame = pd.read_csv(self.fc25_path).iloc[:1].copy()
        frame["overall"] = 91
        frame.to_csv(fc27_path, index=False)
        import_ea(
            [fc27_path, self.fc25_path],
            self.database_path,
            limit=1,
            show_positions=False,
            review_path=self.root / "review.csv",
        )
        with connect(self.database_path) as connection:
            player = connection.execute("SELECT * FROM players").fetchone()
        self.assertEqual(player["overall"], 91)
        self.assertEqual(player["overall_edition"], "FC27")

    def test_age_nationality_match_uses_snapshot_date(self):
        from src.db import initialize_database

        initialize_database(self.database_path)
        with connect(self.database_path) as connection:
            connection.execute("""
                INSERT INTO players (
                    name, birth_date, nationality, tm_player_id, activity_status
                ) VALUES ('Atacante Teste', '2001-03-04', 'Brazil', 'tm-1', 'active')
                """)
        path = self.root / "FC27_20260912.csv"
        pd.DataFrame(
            [
                {
                    "player_id": "ea-age",
                    "long_name": "Atacante Teste",
                    "player_positions": "ST",
                    "overall": 88,
                    "age": 25,
                    "nationality_name": "Brazil",
                    "club_name": "Clube Europeu",
                }
            ]
        ).to_csv(path, index=False)
        result = import_ea(
            [path],
            self.database_path,
            show_positions=False,
            review_path=self.root / "review.csv",
        )
        self.assertEqual(result[path.name]["matched"], 1)
        self.assertEqual(result[path.name]["new_players"], 0)
        with connect(self.database_path) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM players").fetchone()[0], 1)

    def test_ambiguous_match_is_written_to_review_and_not_inserted(self):
        from src.db import initialize_database

        initialize_database(self.database_path)
        with connect(self.database_path) as connection:
            for tm_id in ("tm-1", "tm-2"):
                connection.execute(
                    """
                    INSERT INTO players (name, birth_date, nationality, tm_player_id)
                    VALUES ('Atacante Teste', '2001-03-04', 'Brazil', ?)
                    """,
                    (tm_id,),
                )
        review_path = self.root / "review.csv"
        result = import_ea(
            [self.fc25_path],
            self.database_path,
            limit=1,
            show_positions=False,
            review_path=review_path,
        )
        self.assertEqual(result[self.fc25_path.name]["ambiguous"], 1)
        with connect(self.database_path) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM players").fetchone()[0], 2)
        self.assertIn("multiple_identity_matches", review_path.read_text(encoding="utf-8-sig"))

    def test_brazilian_ea_only_player_is_excluded(self):
        path = self.root / "FC27_brazil.csv"
        pd.DataFrame(
            [
                {
                    "player_id": "ea-br",
                    "long_name": "Jogador Brasil",
                    "player_positions": "ST",
                    "overall": 80,
                    "dob": "2000-01-01",
                    "club_name": "Time X",
                    "league_name": "Campeonato Brasileiro Série A",
                }
            ]
        ).to_csv(path, index=False)
        result = import_ea(
            [path],
            self.database_path,
            show_positions=False,
            review_path=self.root / "review.csv",
        )
        self.assertEqual(result[path.name]["brazil_excluded"], 1)
        with connect(self.database_path) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM players").fetchone()[0], 0)

    def test_women_are_excluded_by_default_and_can_be_included(self):
        path = self.root / "FC27_gender.csv"
        pd.DataFrame(
            [
                {
                    "player_id": "ea-man",
                    "long_name": "Male Player",
                    "player_positions": "ST",
                    "overall": 80,
                    "dob": "2000-01-01",
                    "club_name": "Manchester City",
                    "league_name": "Premier League",
                    "gender": "Men's Football",
                },
                {
                    "player_id": "ea-woman",
                    "long_name": "Female Player",
                    "player_positions": "ST",
                    "overall": 90,
                    "dob": "2000-01-02",
                    "club_name": "Manchester City",
                    "league_name": "Barclays WSL",
                    "gender": "Women's Football",
                },
            ]
        ).to_csv(path, index=False)
        result = import_ea(
            [path],
            self.database_path,
            show_positions=False,
            review_path=self.root / "review.csv",
        )
        self.assertEqual(result[path.name]["players"], 1)
        self.assertEqual(result[path.name]["women_excluded"], 1)
        with connect(self.database_path) as connection:
            names = [row["name"] for row in connection.execute("SELECT name FROM players")]
        self.assertEqual(names, ["Male Player"])

        include_database = self.root / "include-women.sqlite"
        included = import_ea(
            [path],
            include_database,
            show_positions=False,
            review_path=self.root / "review-included.csv",
            include_women=True,
        )
        self.assertEqual(included[path.name]["players"], 2)
        self.assertEqual(included[path.name]["women_excluded"], 0)
        with connect(include_database) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM players").fetchone()[0], 2)

    def test_male_gender_overrides_female_club_name(self):
        path = self.root / "FC27_male_gender.csv"
        pd.DataFrame(
            [
                {
                    "player_id": "ea-man",
                    "long_name": "Male Player",
                    "player_positions": "ST",
                    "overall": 80,
                    "dob": "2000-01-01",
                    "club_name": "Manchester City Women",
                    "league_name": "Premier League",
                    "gender": "Men's Football",
                }
            ]
        ).to_csv(path, index=False)
        result = import_ea(
            [path],
            self.database_path,
            show_positions=False,
            review_path=self.root / "review.csv",
        )
        self.assertEqual(result[path.name]["players"], 1)
        self.assertEqual(result[path.name]["women_excluded"], 0)

    def test_dry_run_does_not_modify_database_and_reports_matches(self):
        initialize_database(self.database_path)
        with connect(self.database_path) as connection:
            for name, birth_date, tm_id, value in (
                ("Matched One", "2000-01-01", "tm-1", 5_000_000),
                ("Matched Two", "1999-01-01", "tm-2", 50_000_000),
                ("Twin Name", "2001-01-01", "tm-3", 1_000_000),
                ("Twin Name", "2001-01-01", "tm-4", 2_000_000),
            ):
                player_id = connection.execute(
                    """
                    INSERT INTO players (
                        name, birth_date, nationality, tm_player_id,
                        market_value_eur, overall_source, activity_status
                    ) VALUES (?, ?, 'Brazil', ?, ?, 'market_log', 'active')
                    """,
                    (name, birth_date, tm_id, value),
                ).lastrowid
                connection.execute(
                    """
                    INSERT INTO player_sources (source, source_id, player_id)
                    VALUES ('transfermarkt', ?, ?)
                    """,
                    (tm_id, player_id),
                )

        path = self.root / "FC27_20260912.csv"
        pd.DataFrame(
            [
                {
                    "player_id": "ea-1",
                    "common_name": "Matched One",
                    "position": "ST",
                    "overall_rating": 90,
                    "birthdate": "2000-01-01",
                    "nationality": "Brazil",
                    "club": "Overseas Club",
                    "league": "Premier League",
                    "edition": "fc27",
                    "snapshot_date": "2026-09-12",
                },
                {
                    "player_id": "ea-2",
                    "common_name": "Matched Two",
                    "position": "CM",
                    "overall_rating": 80,
                    "birthdate": "1999-01-01",
                    "nationality": "Brazil",
                    "club": "Overseas Club",
                    "league": "Premier League",
                    "edition": "fc27",
                    "snapshot_date": "2026-09-12",
                },
                {
                    "player_id": "ea-3",
                    "common_name": "New Player",
                    "position": "CB",
                    "overall_rating": 70,
                    "birthdate": "2002-01-01",
                    "nationality": "Portugal",
                    "club": "European Club",
                    "league": "Primeira Liga",
                    "edition": "fc27",
                    "snapshot_date": "2026-09-12",
                },
                {
                    "player_id": "ea-4",
                    "common_name": "Brazil Player",
                    "position": "ST",
                    "overall_rating": 75,
                    "birthdate": "2002-02-02",
                    "nationality": "Portugal",
                    "club": "Brazilian Club",
                    "league": "Brasileirão Série A",
                    "edition": "fc27",
                    "snapshot_date": "2026-09-12",
                },
                {
                    "player_id": "ea-5",
                    "common_name": "Twin Name",
                    "position": "LW",
                    "overall_rating": 72,
                    "birthdate": "2001-01-01",
                    "nationality": "Brazil",
                    "club": "Overseas Club",
                    "league": "Premier League",
                    "edition": "fc27",
                    "snapshot_date": "2026-09-12",
                },
            ]
        ).to_csv(path, index=False)
        review_path = self.root / "ea-review.csv"

        result = simulate_ea_import(
            [path],
            database_path=self.database_path,
            review_path=review_path,
        )

        counts = result["imports"][path.name]
        self.assertEqual(counts["matched"], 2)
        self.assertEqual(counts["ambiguous"], 1)
        self.assertEqual(counts["new_players"], 1)
        self.assertEqual(counts["brazil_excluded"], 1)
        self.assertEqual(result["comparisons"], 2)
        self.assertIsNotNone(result["correlation"])
        self.assertTrue(review_path.is_file())
        with connect(self.database_path) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM players").fetchone()[0], 4)
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM player_sources").fetchone()[0],
                4,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM player_sources WHERE source LIKE 'ea_%'"
                ).fetchone()[0],
                0,
            )

    def test_dry_run_matches_name_alias_but_regular_import_does_not(self):
        initialize_database(self.database_path)
        with connect(self.database_path) as connection:
            player_id = connection.execute("""
                INSERT INTO players (
                    name, birth_date, nationality, tm_player_id, club_name,
                    market_value_eur, overall, overall_source, activity_status
                ) VALUES (
                    'Vinicius Junior', '2000-07-12', 'Brazil', '371998',
                    'Real Madrid', 140000000, 88, 'market_log', 'active'
                )
                """).lastrowid
            connection.execute(
                """
                INSERT INTO player_sources (source, source_id, player_id)
                VALUES ('transfermarkt', '371998', ?)
                """,
                (player_id,),
            )
        path = self.root / "FC27_alias_20260912.csv"
        pd.DataFrame(
            [
                {
                    "player_id": "ea-vini",
                    "common_name": "Vini Jr.",
                    "first_name": "Vinícius José",
                    "last_name": "de Oliveira Júnior",
                    "position": "LW",
                    "overall_rating": 89,
                    "birthdate": "2000-07-12",
                    "nationality": "Brazil",
                    "club": "Real Madrid",
                    "league": "LALIGA EA SPORTS",
                    "gender": "Men's Football",
                    "edition": "fc27",
                    "snapshot_date": "2026-09-12",
                }
            ]
        ).to_csv(path, index=False)

        dry_run = simulate_ea_import(
            [path],
            database_path=self.database_path,
            review_path=self.root / "dry-run-review.csv",
        )
        counts = dry_run["imports"][path.name]
        self.assertEqual(counts["matched_exact"], 0)
        self.assertEqual(counts["matched_alias"], 1)
        self.assertEqual(counts["ambiguous"], 0)
        self.assertEqual(counts["new_players"], 0)
        self.assertEqual(len(dry_run["alias_matches"]), 1)
        alias = dry_run["alias_matches"][0]
        self.assertEqual(alias["ea_name"], "Vini Jr.")
        self.assertEqual(alias["transfermarkt_name"], "Vinicius Junior")
        self.assertEqual(alias["transfermarkt_id"], "371998")
        self.assertGreaterEqual(alias["score"], 90)
        self.assertEqual(dry_run["top_players"][0]["status"].split()[0], "casado")

        regular = import_ea(
            [path],
            self.database_path,
            show_positions=False,
            review_path=self.root / "regular-review.csv",
        )
        self.assertEqual(regular[path.name]["matched_alias"], 0)
        self.assertEqual(regular[path.name]["new_players"], 1)

    def test_dry_run_never_alias_matches_without_equal_birth_date(self):
        initialize_database(self.database_path)
        with connect(self.database_path) as connection:
            connection.execute("""
                INSERT INTO players (
                    name, birth_date, nationality, tm_player_id
                ) VALUES ('Vinicius Junior', NULL, 'Brazil', '371998')
                """)
        path = self.root / "FC27_alias_no_birth.csv"
        pd.DataFrame(
            [
                {
                    "player_id": "ea-vini",
                    "common_name": "Vini Jr.",
                    "position": "LW",
                    "overall_rating": 89,
                    "birthdate": "2000-07-12",
                    "nationality": "Brazil",
                    "club": "Real Madrid",
                    "league": "LALIGA EA SPORTS",
                    "gender": "Men's Football",
                    "edition": "fc27",
                    "snapshot_date": "2026-09-12",
                }
            ]
        ).to_csv(path, index=False)
        dry_run = simulate_ea_import(
            [path],
            database_path=self.database_path,
            review_path=self.root / "no-birth-review.csv",
        )
        counts = dry_run["imports"][path.name]
        self.assertEqual(counts["matched_alias"], 0)
        self.assertEqual(counts["new_players"], 1)

    def test_dry_run_sends_multiple_strong_aliases_to_review(self):
        initialize_database(self.database_path)
        with connect(self.database_path) as connection:
            for tm_id, player_name in (
                ("371998", "Vinicius Junior"),
                ("999001", "Vinicius da Silva Junior"),
            ):
                connection.execute(
                    """
                    INSERT INTO players (
                        name, birth_date, nationality, tm_player_id
                    ) VALUES (?, '2000-07-12', 'Brazil', ?)
                    """,
                    (player_name, tm_id),
                )
        path = self.root / "FC27_alias_ambiguous.csv"
        pd.DataFrame(
            [
                {
                    "player_id": "ea-vini",
                    "common_name": "Vini Jr.",
                    "position": "LW",
                    "overall_rating": 89,
                    "birthdate": "2000-07-12",
                    "nationality": "Brazil",
                    "club": "Real Madrid",
                    "league": "LALIGA EA SPORTS",
                    "gender": "Men's Football",
                    "edition": "fc27",
                    "snapshot_date": "2026-09-12",
                }
            ]
        ).to_csv(path, index=False)
        review_path = self.root / "ambiguous-alias-review.csv"
        dry_run = simulate_ea_import(
            [path],
            database_path=self.database_path,
            review_path=review_path,
        )
        counts = dry_run["imports"][path.name]
        self.assertEqual(counts["matched_alias"], 0)
        self.assertEqual(counts["ambiguous"], 1)
        self.assertEqual(counts["new_players"], 0)
        review = review_path.read_text(encoding="utf-8-sig")
        self.assertIn("multiple_strong_alias_candidates", review)
        self.assertIn("Vinicius Junior:98.0", review)
        self.assertIn("Vinicius da Silva Junior:98.0", review)

    def test_dry_run_sends_weak_alias_candidate_to_review(self):
        initialize_database(self.database_path)
        with connect(self.database_path) as connection:
            connection.execute("""
                INSERT INTO players (
                    name, birth_date, nationality, tm_player_id
                ) VALUES ('Rafael da Silva Santos', '1995-05-10', 'Brazil', '123456')
                """)
        path = self.root / "FC27_alias_weak.csv"
        pd.DataFrame(
            [
                {
                    "player_id": "ea-rafael",
                    "common_name": "Rafael Santos",
                    "position": "CB",
                    "overall_rating": 75,
                    "birthdate": "1995-05-10",
                    "nationality": "Portugal",
                    "club": "European Club",
                    "league": "European League",
                    "gender": "Men's Football",
                    "edition": "fc27",
                    "snapshot_date": "2026-09-12",
                }
            ]
        ).to_csv(path, index=False)
        review_path = self.root / "weak-alias-review.csv"
        dry_run = simulate_ea_import(
            [path],
            database_path=self.database_path,
            review_path=review_path,
        )
        counts = dry_run["imports"][path.name]
        self.assertEqual(counts["matched_alias"], 0)
        self.assertEqual(counts["ambiguous"], 1)
        self.assertEqual(counts["new_players"], 0)
        review = review_path.read_text(encoding="utf-8-sig")
        self.assertIn("weak_alias_candidate", review)
        self.assertIn("Rafael da Silva Santos:85.5", review)


if __name__ == "__main__":
    unittest.main()
