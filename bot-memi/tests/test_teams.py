import tempfile
import unittest
from pathlib import Path

from src.db import connect, initialize_database
from src.game import claim_player, register_user
from src.lineup_image import render_lineup
from src.teams import (
    FORMATIONS,
    TeamError,
    ensure_team,
    get_lineup,
    get_valid_lineup,
    scale_player,
    set_formation,
    set_team_field,
    set_team_style,
)


class TeamTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.tempdir.name) / "teams.sqlite"
        initialize_database(self.database_path)

    def tearDown(self):
        self.tempdir.cleanup()

    def add_full_team(self, user_id, overall=80):
        register_user(user_id, f"Pessoa {user_id}", self.database_path)
        ensure_team(user_id, f"Pessoa {user_id}", self.database_path)
        for index, position in enumerate(FORMATIONS["4-3-3"]):
            with connect(self.database_path) as connection:
                player_id = connection.execute(
                    """
                    INSERT INTO players (
                        name, display_name, game_position, overall,
                        activity_status, phase2_candidate
                    ) VALUES (?, ?, ?, ?, 'active', 0)
                    """,
                    (f"Jogador {user_id}-{index}", f"Jogador {index}", position, overall),
                ).lastrowid
            self.assertTrue(claim_player(player_id, user_id, self.database_path))
            scale_player(user_id, player_id, self.database_path)

    def test_all_documented_formations_have_eleven_positions(self):
        self.assertGreaterEqual(len(FORMATIONS), 10)
        for formation, positions in FORMATIONS.items():
            with self.subTest(formation=formation):
                self.assertEqual(len(positions), 11)
                self.assertIn("GOL", positions)

    def test_scaleup_validates_ownership_and_exact_starting_positions(self):
        self.add_full_team(100)
        team, players = get_valid_lineup(100, self.database_path)
        self.assertEqual(team["formation"], "4-3-3")
        self.assertEqual(len(players), 11)
        self.assertEqual(players[0]["slot_order"], 1)

    def test_formation_style_and_team_profile_can_be_changed(self):
        self.add_full_team(100)
        set_formation(100, "4-3-2-1", self.database_path)
        set_team_style(100, "contra-ataque", self.database_path)
        set_team_field(100, "team_name", "Memi FC", self.database_path)
        set_team_field(100, "color", "#123ABC", self.database_path)
        team, lineup = get_valid_lineup(100, self.database_path)
        self.assertEqual(team["team_name"], "Memi FC")
        self.assertEqual(team["color"], "#123ABC")
        self.assertEqual(team["style"], "counter")
        self.assertEqual(len(lineup), 11)

    def test_incompatible_formation_and_unsafe_image_path_are_rejected(self):
        self.add_full_team(100)
        with self.assertRaises(TeamError):
            set_formation(100, "unknown", self.database_path)
        with self.assertRaises(TeamError):
            set_team_field(
                100,
                "logo_path",
                "..\\..\\.env",
                self.database_path,
            )
        with self.assertRaises(TeamError):
            set_team_field(100, "color", "green", self.database_path)

    def test_lineup_image_is_a_rendered_png(self):
        self.add_full_team(100)
        team, lineup = get_valid_lineup(100, self.database_path)
        image = render_lineup(team, lineup)
        self.assertEqual(image.read(8), b"\x89PNG\r\n\x1a\n")


if __name__ == "__main__":
    unittest.main()
