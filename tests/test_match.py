import csv
import tempfile
import unittest
from pathlib import Path

from src.db import connect, initialize_database
from src.match import find_matches


class PlayerMatchingTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.database_path = self.root / "memi.sqlite"
        self.review_path = self.root / "review" / "matches.csv"
        initialize_database(self.database_path)

    def tearDown(self):
        self.temporary_directory.cleanup()

    def _insert_player(
        self, source, source_id, name, birth_date, club_name, club_id=None
    ):
        with connect(self.database_path) as connection:
            if club_id is None and club_name:
                club_id = connection.execute(
                    "INSERT INTO clubs (name) VALUES (?)", (club_name,)
                ).lastrowid
            player_id = connection.execute(
                "INSERT INTO players (name, birth_date, club_id) VALUES (?, ?, ?)",
                (name, birth_date, club_id),
            ).lastrowid
            connection.execute(
                """
                INSERT INTO player_sources (
                    source, source_id, player_id, source_name, source_club_name
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (source, source_id, player_id, name, club_name),
            )
        return player_id

    def test_creates_fuzzy_candidates_for_review_without_merging(self):
        player_a = self._insert_player(
            "transfermarkt",
            "tm-1",
            "João Silva",
            "2000-01-02",
            "São Paulo FC",
        )
        player_b = self._insert_player(
            "ea_fc25",
            "ea-1",
            "Joao da Silva",
            "2000-01-02",
            "Sao Paulo",
        )
        candidate_count = find_matches(
            self.database_path, self.review_path, threshold=70
        )

        self.assertEqual(candidate_count, 1)
        with connect(self.database_path) as connection:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM players").fetchone()[0], 2
            )
            candidate = connection.execute(
                "SELECT * FROM player_match_candidates"
            ).fetchone()
            self.assertEqual(candidate["status"], "pending")
            self.assertEqual(
                {candidate["player_a_id"], candidate["player_b_id"]},
                {player_a, player_b},
            )
            self.assertEqual(candidate["birth_date_match"], 1)
            self.assertEqual(candidate["club_match"], 1)
        with self.review_path.open(encoding="utf-8-sig", newline="") as file:
            row = next(csv.DictReader(file))
        self.assertEqual(row["status"], "pending")
        self.assertEqual(row["name_a"], "João Silva")

    def test_mismatching_birth_dates_are_not_candidates(self):
        self._insert_player(
            "transfermarkt", "tm-2", "Alex Silva", "2000-01-02", "Clube A"
        )
        self._insert_player("ea_fc24", "ea-2", "Alex Silva", "2001-01-02", "Clube A")
        self.assertEqual(
            find_matches(self.database_path, self.review_path, threshold=50), 0
        )

    def test_candidate_requires_shared_birth_date_or_club(self):
        self._insert_player("transfermarkt", "tm-3", "Alex Silva", None, "Clube A")
        self._insert_player("ea_fc25", "ea-3", "Alex Silva", None, "Clube B")
        self.assertEqual(
            find_matches(self.database_path, self.review_path, threshold=50), 0
        )

    def test_invalid_threshold_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "entre 0 e 100"):
            find_matches(self.database_path, self.review_path, threshold=101)


if __name__ == "__main__":
    unittest.main()
