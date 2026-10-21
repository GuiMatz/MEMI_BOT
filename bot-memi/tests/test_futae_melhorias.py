"""Testes das melhorias do FUT.AE: upgrades, crates, escalação, duplicados e catálogo."""

import random
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from src.db import connect, initialize_database
from src.dedupe import find_duplicates, remove_duplicates
from src.economy import (
    CRATE_PRICE,
    UPGRADE_LABELS,
    UPGRADE_LIMITS,
    buy_upgrade,
    get_account_status,
    get_upgrade_cost,
    open_crate,
)
from src.game import (
    claim_player,
    get_catalog_entries,
    get_wallet,
    pretty_asset_name,
    register_user,
    search_catalog,
    search_players,
)
from src.lineup_image import render_lineup, slot_coordinates
from src.photos import sync_existing_photos
from src.simulation import penalty_events, penalty_shootout_kicks, simulate_match
from src.teams import (
    FORMATIONS,
    TeamError,
    auto_fill_lineup,
    clear_lineup_slot,
    ensure_team,
    get_lineup,
    set_formation,
    set_lineup_slot,
)


class DatabaseCase(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.tempdir.name) / "game.sqlite"
        initialize_database(self.database_path)
        register_user(100, "Pessoa", self.database_path)

    def tearDown(self):
        self.tempdir.cleanup()

    def add_player(self, name="Jogador", position="CA", overall=70, **extra):
        values = {
            "name": name,
            "display_name": name,
            "game_position": position,
            "overall": overall,
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

    def give_memilets(self, amount):
        with connect(self.database_path) as connection:
            connection.execute(
                "UPDATE game_users SET memilets = ? WHERE discord_user_id = '100'", (amount,)
            )


class UpgradeAndCrateTests(DatabaseCase):
    def test_every_listed_upgrade_has_a_price_or_is_crate_only(self):
        # Regressão: mm!upgrade quebrava ao listar o bônus de duplicata.
        for key in UPGRADE_LABELS:
            if key == "star_wishes":
                continue
            with self.subTest(key=key):
                self.assertGreater(get_upgrade_cost(key, 0), 0)

    def test_duplicate_bonus_can_be_bought(self):
        self.give_memilets(10_000)
        purchase = buy_upgrade(100, "Pessoa", "duplicate_bonus", self.database_path)
        self.assertEqual((purchase.level, purchase.cost), (1, 4_000))
        self.assertEqual(UPGRADE_LIMITS["duplicate_bonus"], 5)

    def test_crate_memilets_never_return_less_than_350(self):
        self.give_memilets(CRATE_PRICE * 200)
        rng = random.Random(7)
        kinds = set()
        for _ in range(200):
            reward = open_crate(100, "Pessoa", self.database_path, rng=rng)
            kinds.add(reward.kind)
            if reward.kind in {"memilets", "jackpot"}:
                self.assertGreaterEqual(reward.amount, 350)
        self.assertIn("memilets", kinds)
        self.assertIn("roll_tokens", kinds)

    def test_claim_reset_clears_an_active_claim_cooldown(self):
        self.give_memilets(CRATE_PRICE)
        recent = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat(timespec="seconds")
        with connect(self.database_path) as connection:
            connection.execute(
                "UPDATE game_users SET last_claim_at = ? WHERE discord_user_id = '100'", (recent,)
            )
        rng = random.Random()
        with patch.object(rng, "choices", return_value=["claim_reset"]):
            reward = open_crate(100, "Pessoa", self.database_path, rng=rng)
        self.assertEqual(reward.kind, "claim_reset")
        self.assertIsNone(get_account_status(100, "Pessoa", self.database_path).claim_available_at)

    def test_claim_reset_without_cooldown_turns_into_rolls(self):
        self.give_memilets(CRATE_PRICE)
        rng = random.Random(3)
        with patch.object(rng, "choices", return_value=["claim_reset"]):
            reward = open_crate(100, "Pessoa", self.database_path, rng=rng)
        self.assertEqual(reward.kind, "roll_tokens")
        self.assertEqual(get_wallet(100, self.database_path)["memilets"], 0)

    def test_account_status_reports_rolls_and_wishes(self):
        status = get_account_status(100, "Pessoa", self.database_path)
        self.assertEqual((status.rolls_left, status.roll_capacity), (10, 10))
        self.assertEqual((status.wish_capacity, status.star_wish_capacity), (5, 1))
        self.assertIsNone(status.daily_available_at)


class LineupTests(DatabaseCase):
    def setUp(self):
        super().setUp()
        ensure_team(100, "Pessoa", self.database_path)

    def own(self, position, overall=70, name=None):
        player_id = self.add_player(name or f"{position} {overall}", position, overall)
        self.assertTrue(claim_player(player_id, 100, self.database_path))
        return player_id

    def test_slot_accepts_only_matching_position_and_replaces_occupant(self):
        first = self.own("GOL", 70)
        second = self.own("GOL", 80)
        striker = self.own("CA", 85)
        set_lineup_slot(100, 1, first, self.database_path)
        set_lineup_slot(100, 1, second, self.database_path)
        lineup = get_lineup(100, self.database_path)
        self.assertEqual([player["id"] for player in lineup], [second])
        with self.assertRaises(TeamError):
            set_lineup_slot(100, 1, striker, self.database_path)
        self.assertTrue(clear_lineup_slot(100, 1, self.database_path))
        self.assertEqual(get_lineup(100, self.database_path), [])

    def test_auto_fill_uses_best_players_of_each_position(self):
        for position in FORMATIONS["4-3-3"]:
            self.own(position, 60)
        best_keeper = self.own("GOL", 88, "Goleiro bom")
        filled = auto_fill_lineup(100, self.database_path)
        lineup = get_lineup(100, self.database_path)
        self.assertEqual(filled, 11)
        self.assertEqual(len(lineup), 11)
        self.assertEqual(lineup[0]["id"], best_keeper)

    def test_formation_change_can_drop_players_without_slot(self):
        for position in FORMATIONS["4-3-3"]:
            self.own(position, 70)
        auto_fill_lineup(100, self.database_path)
        with self.assertRaises(TeamError):
            set_formation(100, "4-4-2", self.database_path)
        dropped = set_formation(100, "4-4-2", self.database_path, drop_incompatible=True)
        self.assertGreater(dropped, 0)
        self.assertEqual(len(get_lineup(100, self.database_path)), 11 - dropped)

    def test_every_formation_has_eleven_distinct_spots_and_renders(self):
        for formation, positions in FORMATIONS.items():
            with self.subTest(formation=formation):
                coordinates = slot_coordinates(formation)
                self.assertEqual(sorted(coordinates), list(range(1, 12)))
                self.assertEqual(len(set(coordinates.values())), 11)
                players = [
                    {"slot_order": slot, "position": position, "id": slot,
                     "name": f"Jogador {slot}", "effective_overall": 80}
                    for slot, position in enumerate(positions, start=1)
                    if slot % 3
                ]
                image = render_lineup({"formation": formation, "team_name": "Teste"}, players)
                self.assertEqual(image.read(8), b"\x89PNG\r\n\x1a\n")


class DuplicateTests(DatabaseCase):
    def test_keeps_highest_overall_and_moves_ownership(self):
        low = self.add_player("Neymar Jr", overall=70, birth_date="1992-02-05")
        high = self.add_player("Neymar Jr.", overall=89, birth_date="1992-02-05")
        other = self.add_player("Neymar Jr", overall=60, birth_date="2001-01-01")
        self.add_player("Neymar Jr", overall=50, overall_source="Cartola")
        self.assertTrue(claim_player(low, 100, self.database_path))
        groups = find_duplicates(self.database_path)
        self.assertEqual(len(groups), 1)
        self.assertEqual([player["id"] for player in groups[0]["players"]], [high, low])
        report = remove_duplicates(self.database_path)
        self.assertEqual(report["removed"], 1)
        with connect(self.database_path) as connection:
            owner = connection.execute(
                "SELECT discord_user_id FROM player_ownership WHERE player_id = ?", (high,)
            ).fetchone()
            remaining = {row[0] for row in connection.execute("SELECT id FROM players")}
        self.assertEqual(owner[0], "100")
        self.assertNotIn(low, remaining)
        self.assertIn(other, remaining)

    def test_groups_with_different_owners_are_kept(self):
        first = self.add_player("Mesmo Nome", overall=70, club_name="Clube")
        second = self.add_player("Mesmo Nome", overall=80, club_name="Clube")
        register_user(200, "Outra", self.database_path)
        claim_player(first, 100, self.database_path)
        claim_player(second, 200, self.database_path)
        self.assertTrue(find_duplicates(self.database_path)[0]["conflict"])
        self.assertEqual(remove_duplicates(self.database_path)["skipped"], 1)


class CatalogTests(DatabaseCase):
    def test_asset_names_are_standardized(self):
        self.assertEqual(pretty_asset_name("flamengo_logo_2024-v2"), "Flamengo")
        self.assertEqual(pretty_asset_name("vasco-da-gama"), "Vasco da Gama")
        self.assertEqual(pretty_asset_name("escudo_atletico-MG_60x60"), "Atlético-MG")
        self.assertEqual(pretty_asset_name("SaoPaulo"), "São Paulo")
        self.assertEqual(pretty_asset_name("shaktar_donetsk"), "Shakhtar Donetsk")
        self.assertEqual(pretty_asset_name("Manchester-City"), "Manchester City")

    def test_cartola_top_uses_team_and_mascot_names(self):
        root = Path(self.tempdir.name)
        for folder, name in (("logos", "logo_flamengo.png"), ("mascots", "mascote-flamengo.png")):
            path = root / "data" / "assets" / "cartola" / folder / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"x")
        self.add_player("Craque Cartola", overall=40, overall_source="Cartola")
        with patch("src.game.ROOT", root):
            entries, total = get_catalog_entries("cartola", self.database_path)
            found = search_catalog("flamengo", self.database_path)
        self.assertEqual(total, 3)
        self.assertEqual(entries[0]["overall"], 90)
        self.assertEqual([entry["name"] for entry in entries[1:]], ["Flamengo", "Flamengo (Mascote)"])
        self.assertEqual({item["kind"] for item in found}, {"Logo Cartola", "Mascote"})

    def test_scout_returns_game_overall_owner_and_photo(self):
        player_id = self.add_player("Atacante", overall=60, photo_path="players/1.png")
        claim_player(player_id, 100, self.database_path)
        player = search_players("atacante", self.database_path)[0]
        self.assertEqual(player["overall"], 72)
        self.assertEqual(player["owner_name"], "Pessoa")
        self.assertEqual(player["photo_path"], "players/1.png")

    def test_downloaded_photos_are_linked_to_players(self):
        player_id = self.add_player("Com foto")
        photo_root = Path(self.tempdir.name) / "photos"
        (photo_root / "players").mkdir(parents=True)
        (photo_root / "players" / f"{player_id}.jpg").write_bytes(b"x")
        self.assertEqual(sync_existing_photos(self.database_path, photo_root), 1)
        self.assertEqual(
            search_players(str(player_id), self.database_path)[0]["photo_path"],
            f"players/{player_id}.jpg",
        )


class NarrationTests(unittest.TestCase):
    @staticmethod
    def team(overall):
        return [
            {"id": index, "name": f"J{index}", "position": position, "overall": overall}
            for index, position in enumerate(FORMATIONS["4-3-3"], start=1)
        ]

    def test_match_has_halftime_second_half_and_consistent_score(self):
        result = simulate_match("A", self.team(80), "B", self.team(78), rng=random.Random(4))
        kinds = [event.kind for event in result.events]
        self.assertEqual(kinds[0], "kickoff")
        self.assertEqual(kinds[-1], "fulltime")
        self.assertLess(kinds.index("halftime"), kinds.index("second_half"))
        last = result.events[-1]
        self.assertEqual((last.home_goals, last.away_goals), (result.home_goals, result.away_goals))
        self.assertEqual(kinds.count("goal"), len(result.goals))

    def test_shootout_kicks_end_with_a_winner(self):
        for seed in range(30):
            kicks = penalty_shootout_kicks(self.team(80), self.team(80), rng=random.Random(seed))
            self.assertNotEqual(kicks[-1].home_score, kicks[-1].away_score)
            events = penalty_events(kicks, "A", "B")
            self.assertEqual(events[0]["kind"], "penalties")
            self.assertEqual(len(events), len(kicks) + 1)


if __name__ == "__main__":
    unittest.main()
