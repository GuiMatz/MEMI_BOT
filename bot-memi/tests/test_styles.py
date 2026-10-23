import random
import sqlite3
import tempfile
import unittest
from pathlib import Path

from src.db import connect, initialize_database
from src.game import register_user
from src.simulation import STYLE_BEATS, STYLE_EDGE, STYLES, simulate_match, style_matchup
from src.teams import FORMATIONS, STYLE_LABELS, TeamError, ensure_team, get_team, set_team_style


class StyleCycleTests(unittest.TestCase):
    def test_every_style_beats_two_and_loses_to_two(self):
        self.assertEqual(set(STYLE_BEATS), set(STYLES))
        self.assertEqual(set(STYLE_LABELS), set(STYLES))
        for style, beaten in STYLE_BEATS.items():
            self.assertEqual(len(beaten), 2, style)
            losses = {other for other, wins in STYLE_BEATS.items() if style in wins}
            self.assertEqual(len(losses), 2, style)
            self.assertFalse(beaten & losses, style)  # ninguém vence e perde para o mesmo

    def test_matchup_is_light_and_symmetric(self):
        self.assertEqual(style_matchup("counter", "attack"), 1 + STYLE_EDGE)
        self.assertEqual(style_matchup("attack", "counter"), 1 - STYLE_EDGE)
        self.assertEqual(style_matchup("balanced", "balanced"), 1.0)
        self.assertLessEqual(STYLE_EDGE, 0.05)  # leve: não vira jogo contra time bem mais forte

    def test_new_styles_play_a_match(self):
        team = [
            {"id": i + 1, "name": f"P{i}", "position": p, "natural_position": p, "overall": 80}
            for i, p in enumerate(FORMATIONS["4-3-3"])
        ]
        other = [dict(player, id=player["id"] + 50) for player in team]
        result = simulate_match(
            "A", team, "B", other, home_style="press", away_style="park", rng=random.Random(1)
        )
        self.assertGreaterEqual(result.home_goals, 0)


class StyleStorageTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.tempdir.name) / "s.sqlite"

    def tearDown(self):
        self.tempdir.cleanup()

    def test_old_table_is_migrated_and_accepts_the_new_styles(self):
        initialize_database(self.database_path)
        register_user(1, "Ana", self.database_path)
        ensure_team(1, "Ana", self.database_path)
        set_team_style(1, "contra-ataque", self.database_path)
        # Volta a tabela para a regra antiga, como num banco de antes dos estilos novos.
        with connect(self.database_path) as connection:
            connection.execute("PRAGMA foreign_keys = OFF")
            connection.execute("ALTER TABLE team_tactics RENAME TO old_tactics")
            connection.execute("""
                CREATE TABLE team_tactics (
                    discord_user_id TEXT PRIMARY KEY,
                    formation TEXT NOT NULL DEFAULT '4-3-3',
                    style TEXT NOT NULL DEFAULT 'balanced'
                        CHECK (style IN ('balanced', 'attack', 'counter')),
                    captain_player_id INTEGER,
                    free_kick_player_id INTEGER,
                    penalty_player_id INTEGER
                )
                """)
            connection.execute("INSERT INTO team_tactics SELECT * FROM old_tactics")
            connection.execute("DROP TABLE old_tactics")
        with self.assertRaises(sqlite3.IntegrityError):
            with connect(self.database_path) as connection:
                connection.execute("UPDATE team_tactics SET style = 'park'")
        initialize_database(self.database_path)  # a migração roda aqui
        self.assertEqual(get_team(1, self.database_path)["style"], "counter")  # dado mantido
        for text, key in (("retranca", "park"), ("pressão alta", "press"), ("pressao", "press")):
            set_team_style(1, text, self.database_path)
            self.assertEqual(get_team(1, self.database_path)["style"], key)
        with self.assertRaises(TeamError):
            set_team_style(1, "tiki-taka", self.database_path)


if __name__ == "__main__":
    unittest.main()
