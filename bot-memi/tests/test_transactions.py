import tempfile
import unittest
from pathlib import Path

from src.db import connect, initialize_database
from src.game import claim_player, get_wallet, register_user
from src.transactions import (
    TransactionError,
    create_trade,
    dismiss_player,
    player_market_value,
    settle_trade,
)


class TransactionTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.tempdir.name) / "transactions.sqlite"
        initialize_database(self.database_path)
        register_user(100, "Pessoa A", self.database_path)
        register_user(200, "Pessoa B", self.database_path)

    def tearDown(self):
        self.tempdir.cleanup()

    def add_owned_player(self, user_id, name, overall=80):
        with connect(self.database_path) as connection:
            player_id = connection.execute(
                """
                INSERT INTO players (
                    name, game_position, overall, activity_status, phase2_candidate
                ) VALUES (?, 'CA', ?, 'active', 0)
                """,
                (name, overall),
            ).lastrowid
        self.assertTrue(claim_player(player_id, user_id, self.database_path))
        return player_id

    def test_dismissal_refunds_market_value_and_releases_player(self):
        player_id = self.add_owned_player(100, "Liberado", overall=99)
        refund = dismiss_player(100, player_id, self.database_path)
        self.assertEqual(refund, round(100_000_000 * 0.0005))
        self.assertEqual(get_wallet(100, self.database_path)["memilets"], refund)
        with connect(self.database_path) as connection:
            self.assertIsNone(
                connection.execute(
                    "SELECT 1 FROM player_ownership WHERE player_id = ?",
                    (player_id,),
                ).fetchone()
            )

    def test_trade_acceptance_transfers_players_and_currency_atomically(self):
        offered_id = self.add_owned_player(100, "Oferta")
        requested_id = self.add_owned_player(200, "Pedido")
        with connect(self.database_path) as connection:
            connection.execute(
                "UPDATE game_users SET memilets = 500 WHERE discord_user_id = '100'"
            )
            connection.execute(
                "UPDATE game_users SET memilets = 300 WHERE discord_user_id = '200'"
            )
        offer_id = create_trade(
            100,
            "Pessoa A",
            200,
            "Pessoa B",
            offered_id,
            requested_id,
            100,
            50,
            self.database_path,
        )
        self.assertEqual(
            settle_trade(offer_id, 200, True, self.database_path),
            "accepted",
        )
        self.assertEqual(get_wallet(100, self.database_path)["memilets"], 450)
        self.assertEqual(get_wallet(200, self.database_path)["memilets"], 350)
        with connect(self.database_path) as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT discord_user_id FROM player_ownership WHERE player_id = ?",
                    (offered_id,),
                ).fetchone()["discord_user_id"],
                "200",
            )
            self.assertEqual(
                connection.execute(
                    "SELECT discord_user_id FROM player_ownership WHERE player_id = ?",
                    (requested_id,),
                ).fetchone()["discord_user_id"],
                "100",
            )

    def test_trade_cannot_be_accepted_by_non_recipient(self):
        offered_id = self.add_owned_player(100, "Oferta")
        requested_id = self.add_owned_player(200, "Pedido")
        offer_id = create_trade(
            100, "Pessoa A", 200, "Pessoa B",
            offered_id, requested_id, 0, 0, self.database_path,
        )
        with self.assertRaises(TransactionError):
            settle_trade(offer_id, 100, True, self.database_path)

    def test_trade_fails_without_partial_transfer_when_cash_has_been_spent(self):
        offered_id = self.add_owned_player(100, "Oferta")
        requested_id = self.add_owned_player(200, "Pedido")
        with connect(self.database_path) as connection:
            connection.execute(
                "UPDATE game_users SET memilets = 100 WHERE discord_user_id = '100'"
            )
        offer_id = create_trade(
            100, "Pessoa A", 200, "Pessoa B",
            offered_id, requested_id, 100, 0, self.database_path,
        )
        with connect(self.database_path) as connection:
            connection.execute(
                "UPDATE game_users SET memilets = 0 WHERE discord_user_id = '100'"
            )
        with self.assertRaises(TransactionError):
            settle_trade(offer_id, 200, True, self.database_path)
        with connect(self.database_path) as connection:
            owner = connection.execute(
                "SELECT discord_user_id FROM player_ownership WHERE player_id = ?",
                (offered_id,),
            ).fetchone()["discord_user_id"]
        self.assertEqual(owner, "100")

    def test_market_value_fallback_matches_documented_curve(self):
        self.assertEqual(player_market_value(1, None), 5_000)
        self.assertEqual(player_market_value(99, None), 100_000_000)


if __name__ == "__main__":
    unittest.main()
