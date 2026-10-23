import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.db import connect, initialize_database
from src.game import claim_player, get_wallet, register_user
from src.market import (
    LISTING_DAYS,
    MAX_LISTINGS,
    MarketError,
    buy_listing,
    cancel_listing,
    create_listing,
    fee_for,
    list_listings,
)
from src.teams import ensure_team, get_lineup, set_lineup_slot
from src.transactions import give_players, toggle_protection

NOW = datetime(2026, 10, 23, 15, tzinfo=timezone.utc)


class MarketTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.tempdir.name) / "m.sqlite"
        initialize_database(self.database_path)
        for user_id, name in ((1, "Ana"), (2, "Bia"), (3, "Caio")):
            register_user(user_id, name, self.database_path)
        self.set_balance(2, 100_000)

    def tearDown(self):
        self.tempdir.cleanup()

    def set_balance(self, user_id, amount):
        with connect(self.database_path) as connection:
            connection.execute(
                "UPDATE game_users SET memilets = ? WHERE discord_user_id = ?",
                (amount, str(user_id)),
            )

    def balance(self, user_id):
        return get_wallet(user_id, self.database_path)["memilets"]

    def player(self, name, owner=1, overall=80, position="CA"):
        with connect(self.database_path) as connection:
            player_id = connection.execute(
                "INSERT INTO players (name, game_position, overall, activity_status, "
                "phase2_candidate) VALUES (?, ?, ?, 'active', 0)",
                (name, position, overall),
            ).lastrowid
        claim_player(player_id, owner, self.database_path)
        return player_id

    def test_listing_rules(self):
        star = self.player("Craque")
        with self.assertRaises(MarketError):
            create_listing(1, "Ana", star, 50, self.database_path, NOW)  # preço baixo demais
        with self.assertRaises(MarketError):
            create_listing(2, "Bia", star, 5_000, self.database_path, NOW)  # não é dela
        toggle_protection(1, star, self.database_path)
        with self.assertRaises(MarketError) as error:
            create_listing(1, "Ana", star, 5_000, self.database_path, NOW)
        self.assertIn("protegido", str(error.exception))
        toggle_protection(1, star, self.database_path)
        create_listing(1, "Ana", star, 5_000, self.database_path, NOW)
        with self.assertRaises(MarketError):
            create_listing(1, "Ana", star, 6_000, self.database_path, NOW)  # já anunciado
        for index in range(MAX_LISTINGS - 1):
            create_listing(1, "Ana", self.player(f"J{index}"), 1_000, self.database_path, NOW)
        with self.assertRaises(MarketError):
            create_listing(1, "Ana", self.player("Sobra"), 1_000, self.database_path, NOW)

    def test_buying_moves_the_card_and_pays_the_seller_minus_the_fee(self):
        star = self.player("Craque")
        ensure_team(1, "Ana", self.database_path)
        set_lineup_slot(1, 11, star, self.database_path)  # CA titular
        listing = create_listing(1, "Ana", star, 20_000, self.database_path, NOW)
        with self.assertRaises(MarketError):
            buy_listing(1, "Ana", listing, self.database_path, NOW)  # o próprio anúncio
        seller_before = self.balance(1)
        purchase = buy_listing(2, "Bia", listing, self.database_path, NOW)
        self.assertEqual((purchase.price, purchase.fee), (20_000, fee_for(20_000)))
        self.assertEqual(self.balance(2), 80_000)
        self.assertEqual(self.balance(1), seller_before + 20_000 - fee_for(20_000))
        with connect(self.database_path) as connection:
            owner = connection.execute(
                "SELECT discord_user_id FROM player_ownership WHERE player_id = ?", (star,)
            ).fetchone()[0]
        self.assertEqual(owner, "2")
        self.assertEqual(get_lineup(1, self.database_path), [])  # saiu do time de quem vendeu
        with self.assertRaises(MarketError):
            buy_listing(3, "Caio", listing, self.database_path, NOW)  # já vendido

    def test_buyer_needs_money_and_stale_or_expired_listings_disappear(self):
        star = self.player("Craque")
        listing = create_listing(1, "Ana", star, 200_000, self.database_path, NOW)
        with self.assertRaises(MarketError) as error:
            buy_listing(2, "Bia", listing, self.database_path, NOW)
        self.assertIn("precisa", str(error.exception))
        # Doou a carta: o anúncio some da lista e não dá para comprar.
        give_players(1, "Ana", 3, "Caio", [star], 0, self.database_path)
        self.assertEqual(list_listings(self.database_path, now=NOW), [])
        with self.assertRaises(MarketError):
            buy_listing(2, "Bia", listing, self.database_path, NOW)
        other = self.player("Outro")
        old = create_listing(1, "Ana", other, 1_000, self.database_path, NOW)
        later = NOW + timedelta(days=LISTING_DAYS, minutes=1)
        self.assertEqual(list_listings(self.database_path, now=later), [])
        with self.assertRaises(MarketError):
            buy_listing(2, "Bia", old, self.database_path, later)
        create_listing(1, "Ana", other, 1_500, self.database_path, later)  # pode anunciar de novo

    def test_cancel_and_sorting(self):
        cheap = create_listing(
            1, "Ana", self.player("Barato", overall=60), 1_000, self.database_path, NOW
        )
        create_listing(1, "Ana", self.player("Caro", overall=85), 9_000, self.database_path, NOW)
        names = [
            item["name"] for item in list_listings(self.database_path, sort="baratos", now=NOW)
        ]
        self.assertEqual(names, ["Barato", "Caro"])
        best = [
            item["name"] for item in list_listings(self.database_path, sort="melhores", now=NOW)
        ]
        self.assertEqual(best[0], "Caro")
        with self.assertRaises(MarketError):
            cancel_listing(2, cheap, self.database_path)  # não é dela
        cancel_listing(1, cheap, self.database_path)
        self.assertEqual(len(list_listings(self.database_path, now=NOW)), 1)


class MarketViewTests(unittest.IsolatedAsyncioTestCase):
    async def test_market_panel_lists_and_offers_to_buy(self):
        from types import SimpleNamespace

        from src.cogs.trades import MarketView, MyListingsView, TradesCog

        tempdir = tempfile.TemporaryDirectory()
        database_path = Path(tempdir.name) / "m.sqlite"
        initialize_database(database_path)
        register_user(1, "Ana", database_path)
        with connect(database_path) as connection:
            player_id = connection.execute(
                "INSERT INTO players (name, game_position, overall, activity_status, "
                "phase2_candidate) VALUES ('Craque', 'CA', 80, 'active', 0)"
            ).lastrowid
        claim_player(player_id, 1, database_path)
        create_listing(1, "Ana", player_id, 5_000, database_path)
        cog = TradesCog(SimpleNamespace(database_path=database_path))
        view = MarketView(cog, SimpleNamespace(id=2))
        self.assertIn("Craque", view.embed().description)
        self.assertEqual(len(view.children[1].options), 1)  # menu de compra
        mine = MyListingsView(cog, SimpleNamespace(id=1))
        self.assertIn("Craque", mine.embed().description)
        tempdir.cleanup()


if __name__ == "__main__":
    unittest.main()
