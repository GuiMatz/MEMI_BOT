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

    def test_rejects_negative_market_value(self):
        with self.assertRaisesRegex(ValueError, "não pode ser negativo"):
            estimate_overall(-1)

    def test_recalculation_preserves_ea_and_applies_manual_override(self):
        with connect(self.database_path) as connection:
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
                INSERT INTO players (name, club_name, market_value_eur)
                VALUES (?, ?, ?)
                """,
                ("Estimado", "Clube Teste", 100_000_000),
            ).lastrowid
            connection.execute(
                """
                INSERT INTO overall_adjustments (player_id, overall, reason)
                VALUES (?, ?, ?)
                """,
                (ea_id, 91, "Ajuste manual de estrela"),
            )

        result = recalculate_overalls(self.database_path)
        self.assertEqual(result, {"manual": 1, "ea_preserved": 0, "estimated": 2, "skipped": 0})
        with connect(self.database_path) as connection:
            rows = {
                row["name"]: (row["overall"], row["overall_source"])
                for row in connection.execute("SELECT name, overall, overall_source FROM players")
            }
        self.assertEqual(rows["EA"], (91, "manual"))
        self.assertEqual(rows["Ajustado"], (88, "market_log"))
        self.assertEqual(rows["Estimado"], (88, "market_log"))

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
            row = connection.execute("SELECT overall, overall_source FROM players").fetchone()
        self.assertEqual((row["overall"], row["overall_source"]), (86, "ea_fc24"))


if __name__ == "__main__":
    unittest.main()
