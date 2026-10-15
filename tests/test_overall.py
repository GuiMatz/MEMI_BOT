import tempfile
import unittest
from pathlib import Path

from src.db import connect, initialize_database
from src.overall import estimate_overall, recalculate_overalls


class OverallTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary_directory.name) / "memi.sqlite"
        initialize_database(self.database_path)

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_market_value_log_scale_is_clamped(self):
        self.assertEqual(estimate_overall(5_000), 30)
        self.assertEqual(estimate_overall(100_000_000), 88)
        self.assertEqual(estimate_overall(1), 30)
        self.assertEqual(estimate_overall(200_000_000), 88)

    def test_league_weight_adjusts_market_estimate_and_clamps_to_99(self):
        self.assertEqual(estimate_overall(100_000_000, 1.1), 97)
        self.assertEqual(estimate_overall(100_000_000, 1.5), 99)

    def test_rejects_negative_value_and_invalid_weight(self):
        with self.assertRaisesRegex(ValueError, "não pode ser negativo"):
            estimate_overall(-1)
        with self.assertRaisesRegex(ValueError, "peso da liga"):
            estimate_overall(100_000, 1.6)

    def test_recalculation_preserves_ea_and_applies_manual_override(self):
        with connect(self.database_path) as connection:
            club_id = connection.execute(
                "INSERT INTO clubs (name) VALUES (?)", ("Clube Teste",)
            ).lastrowid
            league_id = connection.execute(
                "INSERT INTO leagues (name) VALUES (?)", ("Liga Teste",)
            ).lastrowid
            connection.execute(
                "UPDATE clubs SET league_id = ? WHERE id = ?", (league_id, club_id)
            )
            connection.execute(
                "INSERT INTO league_overall_weights (league_id, weight) VALUES (?, ?)",
                (league_id, 1.1),
            )
            ea_id = connection.execute(
                """
                INSERT INTO players (name, overall, overall_source, market_value_eur)
                VALUES (?, ?, ?, ?)
                """,
                ("EA", 87, "ea_fc25", 100_000_000),
            ).lastrowid
            manual_id = connection.execute(
                "INSERT INTO players (name, market_value_eur) VALUES (?, ?)",
                ("Ajustado", 100_000_000),
            ).lastrowid
            estimated_id = connection.execute(
                """
                INSERT INTO players (name, club_id, market_value_eur)
                VALUES (?, ?, ?)
                """,
                ("Estimado", club_id, 100_000_000),
            ).lastrowid
            connection.execute(
                """
                INSERT INTO overall_adjustments (player_id, overall, reason)
                VALUES (?, ?, ?)
                """,
                (ea_id, 91, "Ajuste manual de estrela"),
            )

        result = recalculate_overalls(self.database_path)
        self.assertEqual(
            result, {"manual": 1, "ea_preserved": 0, "estimated": 2, "skipped": 0}
        )
        with connect(self.database_path) as connection:
            rows = {
                row["name"]: (row["overall"], row["overall_source"])
                for row in connection.execute(
                    "SELECT name, overall, overall_source FROM players"
                )
            }
        self.assertEqual(rows["EA"], (91, "manual"))
        self.assertEqual(rows["Ajustado"], (88, "market_log"))
        self.assertEqual(rows["Estimado"], (97, "market_log"))

    def test_preserves_ea_overall_without_manual_adjustment(self):
        with connect(self.database_path) as connection:
            connection.execute(
                """
                INSERT INTO players (name, overall, overall_source, market_value_eur)
                VALUES (?, ?, ?, ?)
                """,
                ("EA sem ajuste", 86, "ea_fc24", 100_000_000),
            )
        result = recalculate_overalls(self.database_path)
        self.assertEqual(result["ea_preserved"], 1)
        with connect(self.database_path) as connection:
            row = connection.execute(
                "SELECT overall, overall_source FROM players"
            ).fetchone()
        self.assertEqual((row["overall"], row["overall_source"]), (86, "ea_fc24"))


if __name__ == "__main__":
    unittest.main()
