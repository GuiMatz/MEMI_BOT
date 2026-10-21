"""Testes da parte 2: enciclopédia, roleta ampliada, nomes, logos, liga, ciclo de 1 hora."""

import random
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from src.card_image import render_player_card
from src.clock import BRASILIA, brasilia_time
from src.db import connect, initialize_database
from src.dedupe import find_duplicates
from src.encyclopedia import load_encyclopedia, seed_encyclopedia
from src.game import (
    get_catalog_entries,
    get_collection_left,
    register_user,
    resolve_player,
    search_catalog,
    sync_club_logos,
)
from src.pool import get_roleta_pool
from src.rolls import CYCLE_LENGTH, cycle_start_for, roll_player
from src.teams import TeamError, ensure_team, get_team, set_team_field


class Case(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.tempdir.name) / "game.sqlite"
        initialize_database(self.database_path)

    def tearDown(self):
        self.tempdir.cleanup()

    def add_player(self, name, **extra):
        values = {
            "name": name,
            "display_name": name,
            "game_position": "CA",
            "overall": 70,
            "activity_status": "active",
            "phase2_candidate": 0,
            **extra,
        }
        columns = ", ".join(values)
        marks = ", ".join("?" for _ in values)
        with connect(self.database_path) as connection:
            return connection.execute(
                f"INSERT INTO players ({columns}) VALUES ({marks})", tuple(values.values())
            ).lastrowid


class EncyclopediaTests(Case):
    def test_seed_file_has_every_section(self):
        data = load_encyclopedia()
        self.assertEqual(
            {key: len(items) for key, items in data.items()},
            {"coaches": 100, "legends": 500, "stadiums": 100, "leagues": 100, "mascots": 100},
        )
        self.assertEqual(
            {legend["position"] for legend in data["legends"]},
            {"GOL", "ZAG", "LD", "LE", "MEI", "CA", "PD", "PE"},
        )

    def test_seed_is_idempotent_and_legends_are_90_plus(self):
        first = seed_encyclopedia(self.database_path)
        second = seed_encyclopedia(self.database_path)
        self.assertEqual(first["coaches"], 100)
        self.assertEqual(first["legends_created"], 500)
        self.assertEqual(sum(second.values()), 0)
        with connect(self.database_path) as connection:
            lowest = connection.execute(
                "SELECT MIN(game_overall(overall, overall_source, name)) FROM players"
            ).fetchone()[0]
            pele = connection.execute(
                "SELECT game_overall(overall, overall_source, name) FROM players WHERE name = 'Pelé'"
            ).fetchone()[0]
        self.assertGreaterEqual(lowest, 90)
        self.assertEqual(pele, 99)

    def test_existing_player_is_promoted_instead_of_duplicated(self):
        neymar = self.add_player("Neymar", nationality="Brazil", overall=84, overall_source="EA")
        common = self.add_player("Marcos", nationality="Brazil", overall=55, overall_source="EA")
        report = seed_encyclopedia(self.database_path)
        self.assertEqual(report["legends_promoted"], 1)
        with connect(self.database_path) as connection:
            rows = {
                row["id"]: dict(row)
                for row in connection.execute(
                    "SELECT id, name, display_name, overall, overall_source FROM players "
                    "WHERE name IN ('Neymar', 'Marcos')"
                )
            }
        self.assertEqual(rows[neymar]["overall_source"], "Lenda")
        self.assertEqual(rows[common]["overall"], 55)
        legend_marcos = next(row for row in rows.values() if row["display_name"] == "Marcos (Lenda)")
        self.assertEqual(legend_marcos["overall_source"], "Lenda")
        self.assertEqual(find_duplicates(self.database_path), [])

    def test_catalog_lists_and_scouts_encyclopedia_items(self):
        seed_encyclopedia(self.database_path)
        mascots, total = get_catalog_entries("mascotes", self.database_path, limit=500)
        self.assertEqual(total, 100)
        self.assertIn("Urubu (Flamengo)", [entry["name"] for entry in mascots])
        kinds = {item["kind"] for item in search_catalog("maracana", self.database_path)}
        self.assertIn("Estádio", kinds)
        self.assertIn("Técnico", {item["kind"] for item in search_catalog("guardiola", self.database_path)})


class PoolTests(Case):
    def test_unknown_activity_phase2_and_missing_overall_enter_the_roulette(self):
        self.add_player("Ativo")
        self.add_player("Desconhecido", activity_status="unknown", phase2_candidate=1)
        self.add_player("Sem nota", overall=None, activity_status="unknown")
        self.add_player("Inativo", activity_status="inactive")
        self.add_player("Sem posição", game_position=None)
        pool = {player["name"]: player["overall"] for player in get_roleta_pool(self.database_path)}
        self.assertEqual(set(pool), {"Ativo", "Desconhecido", "Sem nota"})
        self.assertEqual(pool["Sem nota"], 60)
        self.assertEqual(get_collection_left(self.database_path)["jogadores_total"], 3)

    def test_roll_can_draw_a_player_without_overall(self):
        self.add_player("Sem nota", overall=None, activity_status="unknown", phase2_candidate=1)
        result = roll_player(
            1, "Pessoa", self.database_path,
            now=datetime(2026, 10, 20, 15, 5, tzinfo=timezone.utc), rng=random.Random(1),
        )
        self.assertEqual(result.player["name"], "Sem nota")
        self.assertEqual(result.effective_overall, 60)


class ResolvePlayerTests(unittest.TestCase):
    PLAYERS = [
        {"id": 1, "name": "Khvicha Kvaratskhelia", "overall": 88},
        {"id": 2, "name": "Memi", "overall": 70},
        {"id": 3, "name": "Memis Silva", "overall": 75},
        {"id": 4, "name": "João Pedro", "display_name": "João Pedro (Chelsea · 2001)", "overall": 80},
        {"id": 5, "name": "João Pedro", "display_name": "João Pedro (Brighton · 2002)", "overall": 78},
    ]

    def test_exact_name_wins_even_with_partial_matches(self):
        self.assertEqual(resolve_player("memi", self.PLAYERS)[0]["id"], 2)

    def test_nickname_finds_the_obvious_player(self):
        self.assertEqual(resolve_player("KVARA", self.PLAYERS)[0]["id"], 1)

    def test_homonyms_need_a_choice_or_id(self):
        player, candidates = resolve_player("joao pedro", self.PLAYERS)
        self.assertIsNone(player)
        self.assertEqual([candidate["id"] for candidate in candidates], [4, 5])
        self.assertEqual(resolve_player("5", self.PLAYERS)[0]["id"], 5)
        self.assertEqual(resolve_player("ninguem", self.PLAYERS), (None, []))


class LogoAndLeagueTests(Case):
    def test_logos_without_club_are_created_and_existing_ones_linked(self):
        root = Path(self.tempdir.name)
        folder = root / "data" / "assets" / "brasfoot" / "teams" / "escudos"
        folder.mkdir(parents=True)
        (folder / "shaktar_donetsk.png").write_bytes(b"x")
        (folder / "flamengo.png").write_bytes(b"x")
        with connect(self.database_path) as connection:
            connection.execute("INSERT INTO clubs (name) VALUES ('CR Flamengo')")
        with patch("src.game.ROOT", root):
            report = sync_club_logos(self.database_path)
            again = sync_club_logos(self.database_path)
        self.assertEqual(report, {"linked": 1, "created": 1})
        self.assertEqual(again, {"linked": 0, "created": 0})
        with connect(self.database_path) as connection:
            clubs = dict(connection.execute("SELECT name, logo_path FROM clubs").fetchall())
        self.assertIn("Shakhtar Donetsk", clubs)
        self.assertTrue(clubs["CR Flamengo"].endswith("flamengo.png"))

    def test_favorite_league_uses_catalog_name(self):
        seed_encyclopedia(self.database_path)
        register_user(1, "Pessoa", self.database_path)
        ensure_team(1, "Pessoa", self.database_path)
        set_team_field(1, "favorite_league", "brasileirao serie a", self.database_path)
        self.assertEqual(get_team(1, self.database_path)["favorite_league"], "Campeonato Brasileiro Série A")
        with self.assertRaises(TeamError):
            set_team_field(1, "favorite_league", "liga inventada", self.database_path)


class ClockTests(unittest.TestCase):
    def test_rolls_reset_every_full_hour(self):
        self.assertEqual(CYCLE_LENGTH.total_seconds(), 3600)
        start = cycle_start_for(datetime(2026, 10, 20, 15, 59, tzinfo=timezone.utc))
        self.assertEqual(start, datetime(2026, 10, 20, 15, 0, tzinfo=timezone.utc))

    def test_times_are_shown_in_brasilia(self):
        moment = datetime(2026, 10, 20, 15, 0, tzinfo=timezone.utc)
        self.assertEqual(brasilia_time(moment), "12:00 (Brasília)")
        self.assertEqual(BRASILIA.utcoffset(None).total_seconds(), -3 * 3600)


class CardTests(unittest.TestCase):
    def test_card_is_a_vertical_png(self):
        from PIL import Image

        card = render_player_card({"id": 0, "name": "Teste", "overall": 91, "game_position": "CA"})
        with Image.open(card) as image:
            self.assertGreater(image.height, image.width)
            self.assertEqual(image.format, "PNG")


if __name__ == "__main__":
    unittest.main()
