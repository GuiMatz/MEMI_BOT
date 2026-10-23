"""Testes do update visual: imagens, missões, temporada, garantia das crates e mm!rt."""

import random
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageChops, ImageStat

from src.boards_image import render_missions, render_season
from src.card_image import render_player_card
from src.championship import play_championship, register_team
from src.club_card import render_club_card
from src.cosmetic_card import render_cosmetic_card
from src.crate_animation import PrizeTile, opening_gif, prize_gif
from src.db import connect, initialize_database
from src.economy import (
    CRATE_PITY_LIMIT,
    CRATE_PRICE,
    DailyCooldown,
    EconomyError,
    claim_daily,
    open_crate,
    reset_claim,
)
from src.encyclopedia import seed_cartola_extra
from src.game import claim_player, register_user
from src.missions import DAILY, WEEKLY, assigned, board, claim_rewards, periods, record_event
from src.rolls import claim_roll_offer, roll_player
from src.score_image import render_score_card
from src.season import parse_month, season_history, season_table
from src.teams import FORMATIONS, ensure_team, scale_player
from src.tower import TowerStatus
from src.tower_image import render_tower

NOW = datetime(2026, 10, 22, 15, 10, tzinfo=timezone.utc)


def is_png(buffer, size=None):
    with Image.open(BytesIO(buffer.getvalue())) as image:
        return image.format == "PNG" and (size is None or image.size == size)


class Case(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.tempdir.name) / "game.sqlite"
        initialize_database(self.database_path)
        register_user(100, "Pessoa", self.database_path)

    def tearDown(self):
        self.tempdir.cleanup()

    def give(self, amount, user_id=100):
        with connect(self.database_path) as connection:
            connection.execute(
                "UPDATE game_users SET memilets = ? WHERE discord_user_id = ?",
                (amount, str(user_id)),
            )

    def add_player(self, name, position="CA", overall=70):
        with connect(self.database_path) as connection:
            return connection.execute(
                """
                INSERT INTO players (name, display_name, game_position, overall,
                                     activity_status, phase2_candidate)
                VALUES (?, ?, ?, ?, 'active', 0)
                """,
                (name, name, position, overall),
            ).lastrowid


class ImageTests(unittest.TestCase):
    def test_every_tier_card_renders(self):
        for overall in (55, 65, 75, 84, 88, 92, 96, 99, 100):
            with self.subTest(overall=overall):
                card = render_player_card(
                    {"id": 0, "name": "Teste", "overall": overall, "game_position": "CA"}
                )
                self.assertTrue(is_png(card, (600, 860)))
        legend = render_player_card(
            {
                "id": 0,
                "name": "Zico",
                "overall": 89,
                "game_position": "MEI",
                "overall_source": "Lenda de Clube",
            }
        )
        self.assertTrue(is_png(legend))

    def test_frames_and_art_are_in_the_repository(self):
        from src.art import artwork
        from src.card_frames import TIER_KEYS

        for tier in TIER_KEYS:
            self.assertIsNotNone(artwork(f"molduras/{tier}"), tier)
        for name in ("emblema", "uniforme", "estadio", "tecnico", "liga", "mascote", "pacote"):
            self.assertIsNotNone(artwork(name), name)

    def test_boards_cards_and_score_render(self):
        team = {"team_name": "Memi FC", "color": "#C8102E", "formation": "4-3-3"}
        self.assertTrue(is_png(render_score_card(team, {"team_name": "Outro"}, 2, 1, [])))
        self.assertTrue(is_png(render_club_card(team, {"form": ["V", "D"]})))
        self.assertTrue(is_png(render_tower(TowerStatus({1: 1, 5: 2}, 3_000), team)))
        for kind in ("emblema", "uniforme", "estadio", "tecnico", "liga", "mascote"):
            self.assertTrue(is_png(render_cosmetic_card({"kind": kind, "name": "Item"})))
        self.assertTrue(is_png(render_missions([], "Pessoa")))
        self.assertTrue(is_png(render_season([], "Outubro de 2026")))

    def test_squad_image_is_vertical_for_every_formation(self):
        from src.squad_image import render_squad, squad_coordinates

        for formation, positions in FORMATIONS.items():
            with self.subTest(formation=formation):
                self.assertEqual(len(squad_coordinates(formation)), 11)
                players = [
                    {
                        "slot_order": slot,
                        "position": position,
                        "id": 0,
                        "name": f"J{slot}",
                        "effective_overall": 80,
                    }
                    for slot, position in enumerate(positions, start=1)
                    if slot != 5
                ]
                image = render_squad({"team_name": "Time", "formation": formation}, players)
                with Image.open(BytesIO(image.getvalue())) as opened:
                    self.assertGreater(opened.height, opened.width)

    def test_crate_gifs_show_the_prize_from_the_first_frame(self):
        with Image.open(BytesIO(opening_gif())) as image:
            self.assertEqual(image.format, "GIF")
            self.assertEqual(image.info.get("loop"), 0)
        tiles = [PrizeTile("basico", "coin", "300", "Memilets")]
        for rarity in ("basico", "lendario"):
            data = prize_gif(rarity, tiles)
            self.assertLess(len(data), 3_000_000)
            with Image.open(BytesIO(data)) as image:
                first = image.convert("RGB").copy()
                image.seek(image.n_frames - 1)
                last = image.convert("RGB")
            # O cartão do prêmio já está no primeiro quadro e não muda até o último.
            box = (246, 140, 394, 316)
            tile = first.crop(box)
            self.assertGreater(max(ImageStat.Stat(tile).extrema[0]), 200)  # tem texto claro
            difference = ImageStat.Stat(ImageChops.difference(tile, last.crop(box))).mean
            self.assertLess(max(difference), 6)


class MissionTests(Case):
    def test_missions_are_stable_per_period_and_count_actions(self):
        first = assigned(100, NOW)
        self.assertEqual(first, assigned(100, NOW + timedelta(minutes=5)))
        self.assertEqual(sum(1 for item in first if item[0] == "diaria"), 3)
        self.assertEqual(sum(1 for item in first if item[0] == "semanal"), 2)
        self.assertEqual(periods(NOW), ("2026-10-22", "2026-W43"))
        for _ in range(200):
            for event in (
                "roll",
                "claim",
                "claim_85",
                "friendly",
                "friendly_win",
                "crate",
                "daily",
                "championship_match",
                "tower",
            ):
                record_event(100, event, self.database_path, now=NOW)
        rows = board(100, self.database_path, now=NOW)
        self.assertTrue(all(row["progress"] == row["goal"] for row in rows))
        total, count = claim_rewards(100, self.database_path, now=NOW)
        expected = sum(mission.reward for _, _, _, mission in assigned(100, NOW))
        self.assertEqual((total, count), (expected, 5))
        self.assertEqual(claim_rewards(100, self.database_path, now=NOW), (0, 0))

    def test_game_actions_feed_missions(self):
        self.add_player("Atacante", overall=90)
        with (
            patch("src.missions.DAILY_COUNT", len(DAILY)),
            patch("src.missions.WEEKLY_COUNT", len(WEEKLY)),
        ):
            claim_daily(100, "Pessoa", self.database_path, now=NOW, rng=random.Random(1))
            offer = roll_player(100, "Pessoa", self.database_path, now=NOW, rng=random.Random(1))
            claim_roll_offer(offer.offer_id, 100, self.database_path, now=NOW)
            progress = {
                row["key"]: row["progress"] for row in board(100, self.database_path, now=NOW)
            }
        self.assertEqual(progress["daily_1"], 1)
        self.assertEqual(progress["rolar_20"], 1)
        self.assertEqual(progress["contratar_1"], 1)
        self.assertEqual(progress["craque"], 1)


class CratePityTests(Case):
    def test_forty_crates_without_rare_guarantee_a_rare(self):
        self.give(CRATE_PRICE * CRATE_PITY_LIMIT)
        with patch("src.economy.CRATE_EXTRA_CHANCES", {}):
            results = [
                open_crate(100, "Pessoa", self.database_path, rng=random.Random(index))
                for index in range(CRATE_PITY_LIMIT)
            ]
        self.assertEqual([result.guaranteed for result in results].count(True), 1)
        self.assertTrue(results[-1].guaranteed)
        self.assertEqual(results[-1].best_rarity, "raro")
        self.assertEqual(results[-1].pity, 0)
        self.assertEqual(results[-2].pity, CRATE_PITY_LIMIT - 1)


class ResetClaimTests(Case):
    def test_rt_needs_upgrade_and_waits_twelve_hours(self):
        with self.assertRaises(EconomyError):
            reset_claim(100, "Pessoa", self.database_path, now=NOW)
        with connect(self.database_path) as connection:
            connection.execute(
                "INSERT INTO user_upgrades (discord_user_id, upgrade_key, level) "
                "VALUES ('100', 'claim_reset', 1)"
            )
        with self.assertRaises(EconomyError):  # claim ainda disponível
            reset_claim(100, "Pessoa", self.database_path, now=NOW)
        first, second = self.add_player("Um"), self.add_player("Dois")
        offer = roll_player(100, "Pessoa", self.database_path, now=NOW, rng=random.Random(1))
        claim_roll_offer(offer.offer_id, 100, self.database_path, now=NOW)
        available = reset_claim(100, "Pessoa", self.database_path, now=NOW)
        self.assertEqual(available, NOW + timedelta(hours=12))
        with connect(self.database_path) as connection:
            connection.execute(
                "UPDATE players SET activity_status = 'inactive' WHERE id = ?",
                (offer.player["id"],),
            )
        again = roll_player(100, "Pessoa", self.database_path, now=NOW, rng=random.Random(2))
        self.assertEqual(
            claim_roll_offer(again.offer_id, 100, self.database_path, now=NOW).status,
            "player_claimed",
        )
        self.assertIn(again.player["id"], {first, second})
        with self.assertRaises(DailyCooldown):
            reset_claim(100, "Pessoa", self.database_path, now=NOW + timedelta(hours=1))


class SeasonTests(Case):
    def full_team(self, user_id, overall):
        register_user(user_id, f"Pessoa {user_id}", self.database_path)
        ensure_team(user_id, f"Pessoa {user_id}", self.database_path)
        for index, position in enumerate(FORMATIONS["4-3-3"]):
            player = self.add_player(f"J{user_id}-{index}", position, overall)
            claim_player(player, user_id, self.database_path)
            scale_player(user_id, player, self.database_path)

    def test_month_table_sums_championships(self):
        self.full_team(100, 90)
        self.full_team(200, 60)
        for day in (NOW, NOW + timedelta(days=1)):
            for user_id in (100, 200):
                register_team(555, user_id, now=day, database_path=self.database_path)
            play_championship(555, now=day, rng=random.Random(4), database_path=self.database_path)
        table = season_table(555, "2026-10", self.database_path)
        self.assertEqual(len(table), 2)
        self.assertEqual(sum(team["titles"] for team in table), 2)
        self.assertEqual(sum(team["played"] for team in table), 8)  # 2 jogos por dia, por time
        self.assertEqual(season_table(555, "2026-09", self.database_path), [])
        history = season_history(555, self.database_path)
        self.assertEqual([entry["month"] for entry in history], ["2026-10"])
        self.assertEqual(history[0]["leader"]["team_name"], table[0]["team_name"])
        self.assertEqual(parse_month("10/2026"), "2026-10")
        self.assertEqual(parse_month("outubro", NOW), "2026-10")


class CartolaTests(Case):
    def test_chequer_joins_the_cartola_category_once(self):
        self.assertEqual(seed_cartola_extra(self.database_path), 1)
        self.assertEqual(seed_cartola_extra(self.database_path), 0)
        with connect(self.database_path) as connection:
            row = connection.execute(
                "SELECT club_name, game_position, game_overall(overall, overall_source, name) AS o "
                "FROM players WHERE name = 'Chequer'"
            ).fetchone()
        self.assertEqual(tuple(row), ("Cheréca FC", "CA", 90))


if __name__ == "__main__":
    unittest.main()
