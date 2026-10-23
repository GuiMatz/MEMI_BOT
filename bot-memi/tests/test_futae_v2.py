"""Testes da V2 do FUT.AE: loja, crates, torre, cosméticos, renda, bilheteria e lendas."""

import random
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from src.championship import CHAMPION_PRIZE, play_championship, register_team
from src.cosmetics import (
    CosmeticError,
    equip,
    give_item,
    grandfather_equipped,
    list_owned,
)
from src.db import connect, initialize_database
from src.economy import (
    CRATE_PRICE,
    buy_upgrade,
    claim_daily,
    get_account_status,
    open_crate,
)
from src.encyclopedia import load_club_legends, seed_club_legends, seed_encyclopedia
from src.game import claim_player, get_wallet, register_user
from src.income import (
    IncomeError,
    box_office_status,
    calculate_income,
    collect_box_office,
    collect_income,
    income_status,
)
from src.lineup_image import render_lineup
from src.perks import BASE_DAILY_COOLDOWN, MIN_DAILY_COOLDOWN
from src.rolls import claim_roll_offer, roll_player
from src.teams import FORMATIONS, TeamError, ensure_team, get_team, scale_player, set_team_field
from src.tower import TowerError, build_floor, destroy_preview, destroy_tower, tower_status
from src.transactions import sell_players

NOW = datetime(2026, 10, 22, 12, 10, tzinfo=timezone.utc)


class Case(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.tempdir.name) / "game.sqlite"
        initialize_database(self.database_path)
        register_user(100, "Pessoa", self.database_path)
        ensure_team(100, "Pessoa", self.database_path)

    def tearDown(self):
        self.tempdir.cleanup()

    def give(self, amount, user_id=100):
        with connect(self.database_path) as connection:
            connection.execute(
                "UPDATE game_users SET memilets = ? WHERE discord_user_id = ?",
                (amount, str(user_id)),
            )

    def wallet(self, user_id=100):
        return get_wallet(user_id, self.database_path)["memilets"]

    def add_player(self, name, position="CA", overall=70):
        with connect(self.database_path) as connection:
            return connection.execute(
                """
                INSERT INTO players (
                    name, display_name, game_position, overall, activity_status,
                    phase2_candidate
                ) VALUES (?, ?, ?, ?, 'active', 0)
                """,
                (name, name, position, overall),
            ).lastrowid

    def full_team(self, user_id=100, overall=80):
        register_user(user_id, f"Pessoa {user_id}", self.database_path)
        ensure_team(user_id, f"Pessoa {user_id}", self.database_path)
        ids = []
        for index, position in enumerate(FORMATIONS["4-3-3"]):
            player_id = self.add_player(f"J{user_id}-{index}", position, overall)
            claim_player(player_id, user_id, self.database_path)
            scale_player(user_id, player_id, self.database_path)
            ids.append(player_id)
        return ids

    def add_catalog(self):
        with connect(self.database_path) as connection:
            connection.execute("INSERT INTO stadiums (name) VALUES ('Maracanã')")
            connection.execute("INSERT INTO coaches (name) VALUES ('Telê Santana')")
            connection.execute("INSERT INTO leagues (name) VALUES ('Premier League')")
            connection.execute("INSERT INTO mascots (name, owner) VALUES ('Urubu', 'Flamengo')")


class UpgradeShopTests(Case):
    def test_shop_and_tower_effects_add_up(self):
        self.give(100_000)
        buy_upgrade(100, "Pessoa", "wishes", self.database_path)
        buy_upgrade(100, "Pessoa", "star_wishes", self.database_path)
        build_floor(100, "Pessoa", 1, self.database_path)
        build_floor(100, "Pessoa", 8, self.database_path)
        status = get_account_status(100, "Pessoa", self.database_path)
        self.assertEqual(status.wish_capacity, 5 + 1 + 2)
        self.assertEqual(status.star_wish_capacity, 1 + 1 + 1)

    def test_daily_cooldown_has_a_floor(self):
        with connect(self.database_path) as connection:
            connection.execute(
                "INSERT INTO user_upgrades (discord_user_id, upgrade_key, level) "
                "VALUES ('100', 'daily', 12)"
            )
            connection.execute(
                "INSERT INTO user_bonuses (discord_user_id, bonus_key, amount) "
                "VALUES ('100', 'daily_minutes', 360)"
            )
            connection.execute(
                "INSERT INTO tower_floors (discord_user_id, floor, times) VALUES ('100', 9, 20)"
            )
        status = get_account_status(100, "Pessoa", self.database_path)
        self.assertEqual(status.daily_cooldown, MIN_DAILY_COOLDOWN)

    def test_old_upgrades_are_converted_and_refunded(self):
        legacy = Path(self.tempdir.name) / "legacy.sqlite"
        initialize_database(legacy)
        with connect(legacy) as connection:
            connection.execute("DELETE FROM schema_version WHERE version = 13")
            connection.execute(
                "INSERT INTO game_users (discord_user_id, display_name) VALUES ('1', 'Antiga')"
            )
            for key, level in (("daily", 7), ("duplicate_bonus", 2), ("rolls", 12)):
                connection.execute(
                    "INSERT INTO user_upgrades (discord_user_id, upgrade_key, level) "
                    "VALUES ('1', ?, ?)",
                    (key, level),
                )
        initialize_database(legacy)
        with connect(legacy) as connection:
            levels = dict(
                connection.execute(
                    "SELECT upgrade_key, level FROM user_upgrades WHERE discord_user_id = '1'"
                ).fetchall()
            )
            balance = connection.execute(
                "SELECT memilets FROM game_users WHERE discord_user_id = '1'"
            ).fetchone()[0]
        self.assertEqual(levels.get("daily_max"), 5)
        self.assertEqual(levels.get("rolls"), 10)
        self.assertNotIn("duplicate_bonus", levels)
        self.assertEqual(levels.get("daily", 0), 0)
        # 2 níveis de daily (5000 cada) + duplicata (4000 + 8000) + rolls 11 e 12.
        self.assertEqual(balance, 2 * 5_000 + 12_000 + 5_000 * (11 + 12))


class CosmeticTests(Case):
    def test_only_owned_cosmetics_can_be_equipped(self):
        self.add_catalog()
        with self.assertRaises(CosmeticError):
            equip(100, "tecnico", "Tele", self.database_path)
        give_item(100, "Pessoa", "tecnico", "Telê Santana", self.database_path)
        self.assertEqual(equip(100, "tecnico", "tele", self.database_path)["name"], "Telê Santana")
        self.assertEqual(get_team(100, self.database_path)["coach_name"], "Telê Santana")
        with self.assertRaises(TeamError):
            set_team_field(100, "stadium_name", "Maracanã", self.database_path)

    def test_each_equipped_cosmetic_adds_250_to_daily_cap(self):
        self.add_catalog()
        for kind, name in (("tecnico", "Telê Santana"), ("liga", "Premier League")):
            give_item(100, "Pessoa", kind, name, self.database_path)
            equip(100, kind, name, self.database_path)
        give_item(100, "Pessoa", "estadio", "Maracanã", self.database_path)
        equip(100, "estadio", "Maracanã", self.database_path)  # estádio não soma no daily
        reward = claim_daily(100, "Pessoa", self.database_path, now=NOW, rng=random.Random(1))
        self.assertEqual(reward.cap, 1_200 + 2 * 250)

    def test_unique_cosmetics_have_a_single_owner_but_leagues_repeat(self):
        from src.cosmetics import grant, owners_of, unowned_items

        self.add_catalog()
        register_user(200, "Outra", self.database_path)
        give_item(100, "Pessoa", "tecnico", "Telê Santana", self.database_path)
        give_item(100, "Pessoa", "liga", "Premier League", self.database_path)
        with self.assertRaises(CosmeticError) as error:
            give_item(200, "Outra", "tecnico", "Telê Santana", self.database_path)
        self.assertIn("Pessoa", str(error.exception))
        give_item(200, "Outra", "liga", "Premier League", self.database_path)  # liga repete
        self.assertEqual(owners_of("tecnico", "Telê Santana", self.database_path), ["Pessoa"])
        self.assertEqual(len(owners_of("liga", "Premier League", self.database_path)), 2)
        with connect(self.database_path) as connection:
            coaches = [
                item["name"]
                for item in unowned_items(connection, 200, "tecnico", self.database_path)
            ]
            self.assertNotIn("Telê Santana", coaches)  # some do sorteio da outra pessoa
            self.assertFalse(grant(connection, 200, "tecnico", {"name": "Telê Santana"}, "crate"))

    def test_crate_never_repeats_a_cosmetic_the_user_already_has(self):
        self.add_catalog()
        for kind, name in (
            ("estadio", "Maracanã"),
            ("tecnico", "Telê Santana"),
            ("liga", "Premier League"),
        ):
            give_item(100, "Pessoa", kind, name, self.database_path)
        self.give(CRATE_PRICE)
        with (
            patch("src.economy.CRATE_EXTRA_CHANCES", {"incomum": 1.0}),
            patch("src.economy.CRATE_ITEM_CHANCE", 1.0),
        ):
            result = open_crate(100, "Pessoa", self.database_path, rng=random.Random(2))
        cosmetic = [prize for prize in result.prizes if prize.kind.startswith("cosmetic:")]
        self.assertEqual([prize.name for prize in cosmetic], ["Urubu (Flamengo)"])
        # Sem nenhum cosmético novo, a caixa vira Memilets.
        self.give(CRATE_PRICE)
        with (
            patch("src.economy.CRATE_EXTRA_CHANCES", {"incomum": 1.0}),
            patch("src.economy.CRATE_ITEM_CHANCE", 1.0),
        ):
            result = open_crate(100, "Pessoa", self.database_path, rng=random.Random(3))
        self.assertTrue(all(prize.kind == "memilets" for prize in result.prizes))
        self.assertEqual(len(list_owned(100, None, self.database_path)), 4)

    def test_equipped_items_are_grandfathered_once(self):
        with connect(self.database_path) as connection:
            connection.execute(
                "UPDATE game_teams SET coach_name = 'Técnico Antigo' WHERE discord_user_id = '100'"
            )
        self.assertEqual(grandfather_equipped(self.database_path), 1)
        self.assertEqual(grandfather_equipped(self.database_path), 0)
        self.assertEqual(
            list_owned(100, "tecnico", self.database_path)[0]["name"], "Técnico Antigo"
        )


class CrateTests(Case):
    def test_rare_tiers_give_permanent_bonuses_and_can_double(self):
        self.give(CRATE_PRICE * 2)
        with (
            patch("src.economy.CRATE_EXTRA_CHANCES", {"epico": 1.0}),
            patch("src.economy.CRATE_ITEM_CHANCE", 1.0),
            patch("src.economy.CRATE_DOUBLE_CHANCE", 1.0),
        ):
            result = open_crate(100, "Pessoa", self.database_path, rng=random.Random(4))
        self.assertTrue(result.doubled)
        self.assertEqual([prize.rarity for prize in result.prizes], ["basico", "epico", "epico"])
        self.assertEqual(result.memilets, 0)

    def test_crate_rolls_are_saved_and_do_not_expire(self):
        self.add_player("Atacante")
        self.give(CRATE_PRICE)
        with (
            patch("src.economy.CRATE_EXTRA_CHANCES", {}),
            patch("src.economy.CRATE_ITEM_CHANCE", 1.0),
            patch("src.economy.CRATE_DOUBLE_CHANCE", 0.0),
        ):
            result = open_crate(100, "Pessoa", self.database_path, now=NOW, rng=random.Random(5))
        rolls = result.prizes[0].amount
        status = get_account_status(100, "Pessoa", self.database_path, now=NOW)
        self.assertEqual((status.roll_capacity, status.roll_tokens), (10, rolls))
        later = get_account_status(100, "Pessoa", self.database_path, now=NOW + timedelta(days=3))
        self.assertEqual(later.roll_tokens, rolls)

    def test_floor_four_and_legendary_discount_lower_the_price(self):
        self.give(10_000)
        build_floor(100, "Pessoa", 4, self.database_path)
        with connect(self.database_path) as connection:
            connection.execute(
                "INSERT INTO user_bonuses (discord_user_id, bonus_key, amount) "
                "VALUES ('100', 'crate_discount', 10)"
            )
        self.assertEqual(get_account_status(100, "Pessoa", self.database_path).crate_price, 465)


class TowerTests(Case):
    def test_floors_cost_more_each_time_and_construction_must_finish(self):
        self.give(1_000_000)
        prices = [build_floor(100, "Pessoa", floor, self.database_path).price for floor in (5, 2)]
        self.assertEqual(prices, [1_000, 1_500])
        with self.assertRaises(TowerError):
            build_floor(100, "Pessoa", 5, self.database_path)
        self.full_team()
        for floor in (1, 3, 4, 6, 7, 8, 9, 10, 11, 12):
            build_floor(100, "Pessoa", floor, self.database_path)
        status = tower_status(100, self.database_path)
        self.assertEqual(status.construction, 2)
        second = build_floor(100, "Pessoa", 5, self.database_path)
        self.assertEqual((second.construction, second.price), (2, 1_000 + 500 * 12))

    def test_destroy_returns_everything_and_removes_the_floor_twelve_ball(self):
        players = self.full_team()
        self.give(10_000)
        build_floor(100, "Pessoa", 1, self.database_path)
        result = build_floor(100, "Pessoa", 12, self.database_path, rng=random.Random(1))
        self.assertIn(result.ball_player["id"], players)
        preview = destroy_preview(100, self.database_path)
        self.assertEqual(preview.refund, 2_500)
        self.assertEqual(len(preview.ball_players), 1)
        destroy_tower(100, self.database_path)
        self.assertEqual(self.wallet(), 10_000)
        with connect(self.database_path) as connection:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM player_balls").fetchone()[0], 0
            )
        self.assertEqual(tower_status(100, self.database_path).total_built, 0)

    def test_floor_twelve_needs_a_lineup(self):
        self.give(10_000)
        with self.assertRaises(TowerError):
            build_floor(100, "Pessoa", 12, self.database_path)
        self.assertEqual(self.wallet(), 10_000)

    def test_floor_twelve_needs_all_eleven_starters(self):
        players = self.full_team()
        with connect(self.database_path) as connection:
            connection.execute("DELETE FROM team_lineups WHERE player_id = ?", (players[-1],))
        self.give(10_000)
        with self.assertRaises(TowerError) as error:
            build_floor(100, "Pessoa", 12, self.database_path)
        self.assertIn("10/11", str(error.exception))

    def test_destroying_does_not_reroll_the_floor_twelve_ball(self):
        self.full_team()
        self.give(10_000)
        first = build_floor(100, "Pessoa", 12, self.database_path, rng=random.Random(1))
        for seed in range(2, 22):
            destroy_tower(100, self.database_path)
            again = build_floor(100, "Pessoa", 12, self.database_path, rng=random.Random(seed))
            self.assertEqual(again.ball_player["id"], first.ball_player["id"])
        with connect(self.database_path) as connection:
            balls = connection.execute("SELECT player_id, balls FROM player_balls").fetchall()
        self.assertEqual([tuple(row) for row in balls], [(first.ball_player["id"], 1)])
        self.assertEqual(self.wallet(), 10_000 - 1_000)

    def test_new_draw_only_if_the_drawn_player_left(self):
        self.full_team()
        self.give(10_000)
        first = build_floor(100, "Pessoa", 12, self.database_path, rng=random.Random(1))
        destroy_tower(100, self.database_path)
        with connect(self.database_path) as connection:
            position = connection.execute(
                "SELECT game_position FROM players WHERE id = ?", (first.ball_player["id"],)
            ).fetchone()[0]
        sell_players(100, [first.ball_player["id"]], self.database_path)
        reserve = self.add_player("Reserva", position, 80)
        claim_player(reserve, 100, self.database_path)
        scale_player(100, reserve, self.database_path)
        again = build_floor(100, "Pessoa", 12, self.database_path, rng=random.Random(3))
        self.assertNotEqual(again.ball_player["id"], first.ball_player["id"])


class RollPerkTests(Case):
    def test_wish_protection_lets_only_wishers_claim_first(self):
        player = self.add_player("Craque")
        register_user(200, "Quem rola", self.database_path)
        with connect(self.database_path) as connection:
            connection.execute(
                "INSERT INTO user_wishes (discord_user_id, player_id, wish_type) "
                "VALUES ('100', ?, 'normal')",
                (player,),
            )
            connection.execute(
                "INSERT INTO user_upgrades (discord_user_id, upgrade_key, level) "
                "VALUES ('100', 'wish_protection', 3)"
            )
        result = roll_player(200, "Quem rola", self.database_path, now=NOW, rng=random.Random(1))
        self.assertEqual(result.wished_by, ("100",))
        self.assertEqual(result.protection_seconds, 3)
        blocked = claim_roll_offer(result.offer_id, 200, self.database_path, now=NOW)
        self.assertEqual(blocked.status, "wish_protected")
        claimed = claim_roll_offer(
            result.offer_id, 100, self.database_path, now=NOW + timedelta(seconds=1)
        )
        self.assertEqual(claimed.status, "player_claimed")

    def test_floor_three_pays_the_overall_on_claims(self):
        player = self.add_player("Bom", overall=90)
        self.give(1_000)
        build_floor(100, "Pessoa", 3, self.database_path)
        result = roll_player(100, "Pessoa", self.database_path, now=NOW, rng=random.Random(1))
        claimed = claim_roll_offer(result.offer_id, 100, self.database_path, now=NOW)
        self.assertEqual((claimed.status, claimed.reward_amount), ("player_claimed", 90))
        self.assertEqual(self.wallet(), 90)
        self.assertEqual(player, result.player["id"])

    def test_floor_two_can_give_two_balls(self):
        player = self.add_player("Repetido")
        claim_player(player, 100, self.database_path)
        self.give(1_000)
        build_floor(100, "Pessoa", 2, self.database_path)
        rng = random.Random(1)
        with patch.object(rng, "random", return_value=0.0):
            result = roll_player(100, "Pessoa", self.database_path, now=NOW, rng=rng)
        self.assertEqual((result.kind, result.balls), ("own_duplicate_double_ball", 2))

    def test_one_claim_per_half_hour_cycle(self):
        first = self.add_player("Um")
        offer = roll_player(100, "Pessoa", self.database_path, now=NOW, rng=random.Random(1))
        claim_roll_offer(offer.offer_id, 100, self.database_path, now=NOW)
        with connect(self.database_path) as connection:
            connection.execute(
                "UPDATE players SET activity_status = 'inactive' WHERE id = ?", (first,)
            )
        second = self.add_player("Dois")
        again = roll_player(
            100, "Pessoa", self.database_path, now=NOW + timedelta(minutes=1), rng=random.Random(2)
        )
        retry = claim_roll_offer(
            again.offer_id, 100, self.database_path, now=NOW + timedelta(minutes=1)
        )
        self.assertEqual(again.player["id"], second)
        self.assertEqual(retry.retry_at, NOW + timedelta(minutes=20))  # 12:10 -> 12:30


class IncomeTests(Case):
    def test_income_rules(self):
        self.assertEqual(calculate_income(2, 1), (100 + 100 + 100, "adorou"))
        self.assertEqual(calculate_income(1, 1), (150, "adorou"))
        self.assertEqual(calculate_income(1, 2), (50, "reembolso"))
        self.assertEqual(calculate_income(0, 3), (0, "saiu"))
        self.assertEqual(calculate_income(0, 1, bonus=50), (50, "reembolso"))

    def test_income_needs_stadium_and_ignores_daily_reductions(self):
        with self.assertRaises(IncomeError):
            collect_income(100, 1, 0, self.database_path, now=NOW)
        self.add_catalog()
        give_item(100, "Pessoa", "estadio", "Maracanã", self.database_path)
        equip(100, "estadio", "Maracanã", self.database_path)
        with connect(self.database_path) as connection:
            connection.execute(
                "INSERT INTO user_upgrades (discord_user_id, upgrade_key, level) "
                "VALUES ('100', 'daily', 12)"
            )
        result = collect_income(100, 2, 0, self.database_path, now=NOW)
        self.assertEqual(result.amount, 300)
        self.assertEqual(result.available_at, NOW + BASE_DAILY_COOLDOWN)
        with self.assertRaises(IncomeError):
            collect_income(100, 2, 0, self.database_path, now=NOW + timedelta(hours=20))
        status = income_status(100, self.database_path, now=NOW)
        self.assertEqual(status.total, 300)
        self.assertFalse(status.ready)


class ChampionshipRewardTests(Case):
    def test_champion_prize_and_box_office(self):
        self.full_team(100, overall=90)
        self.full_team(200, overall=60)
        self.give(10_000, 100)
        build_floor(100, "Pessoa", 5, self.database_path)
        for user_id in (100, 200):
            register_team(555, user_id, now=NOW, database_path=self.database_path)
        before = {user_id: self.wallet(user_id) for user_id in (100, 200)}
        table = play_championship(
            555, now=NOW, rng=random.Random(3), database_path=self.database_path
        )
        winner = int(table["winner_user_id"])
        self.assertEqual(table["prize"], CHAMPION_PRIZE)
        self.assertEqual(self.wallet(winner), before[winner] + CHAMPION_PRIZE)
        box = box_office_status(100, self.database_path)
        self.assertGreaterEqual(box.reserve, 100)
        self.assertEqual(box_office_status(200, self.database_path).reserve, 0)
        collected = collect_box_office(100, self.database_path)
        self.assertEqual(collected, box.reserve)
        self.assertEqual(box_office_status(100, self.database_path).total, collected)


class ClubLegendTests(Case):
    def test_twelve_clubs_with_eleven_unique_legends(self):
        clubs = load_club_legends()
        self.assertEqual(len(clubs), 12)
        names = [player["name"] for club in clubs for player in club["players"]]
        self.assertEqual(len(names), 132)
        self.assertEqual(len(set(names)), 132)
        overalls = {player["overall"] for club in clubs for player in club["players"]}
        self.assertTrue(all(83 <= overall <= 89 for overall in overalls))

    def test_existing_legends_are_not_duplicated(self):
        seed_encyclopedia(self.database_path)
        first = seed_club_legends(self.database_path)
        again = seed_club_legends(self.database_path)
        self.assertEqual(first["club_legends_existing"], again["club_legends_existing"])
        self.assertEqual(again["club_legends_created"], 0)
        with connect(self.database_path) as connection:
            pele = connection.execute(
                "SELECT COUNT(*) FROM players WHERE name = 'Pelé'"
            ).fetchone()[0]
            raul = connection.execute(
                "SELECT club_name, game_position, game_overall(overall, overall_source, name) "
                "FROM players WHERE name = 'Raul Plassmann'"
            ).fetchone()
            pepe = connection.execute(
                "SELECT display_name FROM players WHERE club_name = 'Lendas do Santos' "
                "AND name = 'Pepe'"
            ).fetchone()[0]
            ids = [row[0] for row in connection.execute("SELECT id FROM players")]
        self.assertEqual(pele, 1)
        self.assertEqual(tuple(raul), ("Lendas do Flamengo", "GOL", 83))
        self.assertEqual(pepe, "Pepe (Santos)")
        self.assertEqual(len(ids), len(set(ids)))


class LineupImageTests(Case):
    def test_lineup_renders_with_coach(self):
        self.full_team()
        team = get_team(100, self.database_path)
        team["coach_name"] = "Telê Santana"
        from src.teams import get_lineup

        image = render_lineup(team, get_lineup(100, self.database_path))
        self.assertEqual(image.read(8), b"\x89PNG\r\n\x1a\n")


class MenuTests(unittest.IsolatedAsyncioTestCase):
    """Os menus montam sem erro e começam pelo parágrafo que explica o sistema."""

    async def asyncSetUp(self):
        from types import SimpleNamespace

        self.tempdir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.tempdir.name) / "game.sqlite"
        initialize_database(self.database_path)
        self.bot = SimpleNamespace(database_path=self.database_path)
        self.user = SimpleNamespace(id=100, display_name="Pessoa", mention="<@100>")
        register_user(100, "Pessoa", self.database_path)

    async def asyncTearDown(self):
        self.tempdir.cleanup()

    async def test_menus_have_intro_paragraph(self):
        from src.cogs.club import TOWER_INTRO, ClubCog
        from src.cogs.economy import CRATES_INTRO, UPGRADES_INTRO, EconomyCog

        economy = EconomyCog(self.bot)
        embed, view = economy.upgrade_menu(self.user)
        self.assertTrue(embed.description.startswith(f"*{UPGRADES_INTRO}*"))
        self.assertEqual(len(view.children[0].options), 13)
        embed, _ = economy.crate_menu(self.user)
        self.assertTrue(embed.description.startswith(f"*{CRATES_INTRO}*"))
        club = ClubCog(self.bot)
        embed = club.tower_embed(self.user)
        self.assertTrue(embed.description.startswith(f"*{TOWER_INTRO}*"))
        self.assertIn("`12` ", embed.description)
        self.assertEqual(embed.image.url, "attachment://torre.png")

    async def test_tower_menu_builds_and_destroys_through_confirmation(self):
        from src.cogs.club import ClubCog, TowerView
        from src.cogs.common import ConfirmView

        club = ClubCog(self.bot)
        view = TowerView(club, self.user)
        self.assertEqual(len(view.children), 1)  # sem andares: só o menu, sem destruir
        self.assertEqual(len(view.children[0].options), 12)
        with connect(self.database_path) as connection:
            connection.execute(
                "UPDATE game_users SET memilets = 100000 WHERE discord_user_id = '100'"
            )
        build_floor(100, "Pessoa", 3, self.database_path)
        view = TowerView(club, self.user)
        select, destroy = view.children
        self.assertNotIn("3", [option.value for option in select.options])
        self.assertEqual(destroy.label, "Destruir a torre")
        content, confirm = club.build_prompt(self.user, 5)
        self.assertIsInstance(confirm, ConfirmView)
        self.assertIn("andar **5**", content)
        _, refused = club.build_prompt(self.user, 3)
        self.assertIsNone(refused)
        embed, confirm = club.destroy_prompt(self.user)
        self.assertIn("Andar 3", embed.description)
        self.assertIsInstance(confirm, ConfirmView)

    async def test_purchase_prompt_asks_before_buying(self):
        from src.cogs.economy import EconomyCog, resolve_upgrade

        economy = EconomyCog(self.bot)
        self.assertEqual(resolve_upgrade("olheiro"), "rolls")
        self.assertEqual(resolve_upgrade("Declarar Interesse"), "star_wishes")
        content, view = economy.purchase_prompt(self.user, "rolls")
        self.assertIn("Contratar Olheiro", content)
        self.assertIsNotNone(view)
        self.assertEqual(get_wallet(100, self.database_path)["memilets"], 0)


if __name__ == "__main__":
    unittest.main()
