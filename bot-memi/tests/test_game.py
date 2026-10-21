from concurrent.futures import ThreadPoolExecutor
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.db import connect, initialize_database
from src.game import (
    claim_player,
    get_collection_left,
    get_top_players,
    get_wallet,
    list_players_by_team_or_series,
    list_owned_players,
    register_user,
    search_players,
)


class GameAccountTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary_directory.name) / "memi.sqlite"
        initialize_database(self.database_path)

    def tearDown(self):
        self.temporary_directory.cleanup()

    def add_player(self, **overrides):
        values = {
            "name": "Jogador de teste",
            "display_name": "Jogador de teste",
            "club_name": "Clube de teste",
            "nationality": "Brasil",
            "game_position": "CA",
            "overall": 80,
            "activity_status": "active",
            "phase2_candidate": 0,
        }
        values.update(overrides)
        if "name" in overrides and "display_name" not in overrides:
            values["display_name"] = overrides["name"]
        columns = ", ".join(values)
        placeholders = ", ".join("?" for _ in values)
        with connect(self.database_path) as connection:
            return connection.execute(
                f"INSERT INTO players ({columns}) VALUES ({placeholders})",
                tuple(values.values()),
            ).lastrowid

    def test_register_user_is_idempotent_and_updates_display_name(self):
        first = register_user(123, "Nome antigo", self.database_path)
        second = register_user(123, "Nome atual", self.database_path)

        self.assertEqual(first["memilets"], 0)
        self.assertEqual(second["display_name"], "Nome atual")
        self.assertEqual(
            get_wallet(123, self.database_path)["memilets"],
            0,
        )

    def test_collection_left_counts_unclaimed_eligible_players(self):
        player_id = self.add_player()
        self.add_player(activity_status="inactive")
        register_user(123, "Pessoa", self.database_path)
        self.assertTrue(claim_player(player_id, 123, self.database_path))

        root = Path(self.temporary_directory.name)
        for folder, name in (("teams/camisas", "a.png"), ("teams/camisas", "b.png"), ("teams/escudos", "c.png")):
            path = root / "data" / "assets" / "brasfoot" / folder / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"x")
        with connect(self.database_path) as connection:
            connection.execute(
                "INSERT INTO game_teams (discord_user_id, kit_path) VALUES ('123', ?)",
                ("data/assets/brasfoot/teams/camisas/a.png",),
            )

        with patch("src.game.ROOT", root):
            counts = get_collection_left(self.database_path)

        self.assertEqual(counts["jogadores_total"], 1)
        self.assertEqual(counts["jogadores"], 0)
        self.assertEqual((counts["uniformes"], counts["uniformes_total"]), (1, 2))
        self.assertEqual((counts["logos"], counts["logos_total"]), (1, 1))
        self.assertEqual(sum(free for _, free, _ in counts["series"]), 0)
        self.assertEqual(sum(total for _, _, total in counts["series"]), 1)

    def test_lists_players_by_team_or_series_in_overall_order(self):
        self.add_player(name="Jogador baixo", club_name="Clube Azul", overall=70)
        self.add_player(name="Jogador alto", club_name="Clube Azul", overall=90)
        self.add_player(name="Jogador livre", club_name=None, overall=99)

        players = list_players_by_team_or_series("clube azul", self.database_path)

        self.assertEqual(
            [player["name"] for player in players],
            ["Jogador alto", "Jogador baixo"],
        )


    def test_player_search_ignores_accents_and_roster_requires_ownership(self):
        player_id = self.add_player(
            name="João Félix",
            display_name="João Félix",
        )
        register_user(123, "Pessoa", self.database_path)

        results = search_players("Joao Felix", self.database_path)
        self.assertEqual([row["id"] for row in results], [player_id])
        self.assertEqual(list_owned_players(123, self.database_path), [])

        self.assertTrue(claim_player(player_id, 123, self.database_path))
        roster = list_owned_players(123, self.database_path)
        self.assertEqual([row["id"] for row in roster], [player_id])

    def test_player_search_by_id_disambiguates_duplicate_names(self):
        first_id = self.add_player(
            name="Alex Silva",
            display_name="Alex Silva",
            club_name="Clube A",
        )
        second_id = self.add_player(
            name="Alex Silva",
            display_name="Alex Silva",
            club_name="Clube B",
        )

        results = search_players("Alex Silva", self.database_path)
        self.assertEqual([row["id"] for row in results], [first_id, second_id])
        self.assertEqual(
            [row["id"] for row in search_players(str(second_id), self.database_path)],
            [second_id],
        )

    def test_player_can_only_be_claimed_once(self):
        player_id = self.add_player()
        register_user(123, "Pessoa A", self.database_path)
        register_user(456, "Pessoa B", self.database_path)

        self.assertTrue(claim_player(player_id, 123, self.database_path))
        self.assertFalse(claim_player(player_id, 456, self.database_path))
        with connect(self.database_path) as connection:
            owner = connection.execute(
                "SELECT discord_user_id FROM player_ownership WHERE player_id = ?",
                (player_id,),
            ).fetchone()
        self.assertEqual(owner["discord_user_id"], "123")

    def test_simultaneous_claims_have_only_one_winner(self):
        player_id = self.add_player()
        register_user(123, "Pessoa A", self.database_path)
        register_user(456, "Pessoa B", self.database_path)

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(
                executor.map(
                    lambda user_id: claim_player(
                        player_id,
                        user_id,
                        self.database_path,
                    ),
                    (123, 456),
                )
            )

        self.assertEqual(results.count(True), 1)
        self.assertEqual(results.count(False), 1)

    def test_top_players_excludes_ineligible_records(self):
        self.add_player(name="Elegível", overall=90)
        # Candidatos da fase 2 e atividade desconhecida também entram na roleta.
        self.add_player(
            name="Candidato",
            overall=95,
            phase2_candidate=1,
            activity_status="unknown",
        )
        self.add_player(
            name="Inativo",
            overall=98,
            activity_status="inactive",
        )
        self.add_player(
            name="Sem posição",
            overall=97,
            game_position=None,
        )

        self.assertEqual(
            [player["name"] for player in get_top_players(self.database_path)],
            ["Candidato", "Elegível"],
        )


if __name__ == "__main__":
    unittest.main()
