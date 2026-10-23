import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from src.db import connect, initialize_database
from src.game import claim_player, get_wallet, register_user
from src.teams import ensure_team, get_lineup, set_lineup_slot
from src.transactions import (
    MAX_TRADE_PLAYERS,
    TransactionError,
    cancel_trade,
    create_trade,
    dismiss_player,
    give_players,
    market_values,
    sale_quote,
    sale_value,
    sell_players,
    settle_trade,
    trade_details,
)


class DatabaseCase(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.tempdir.name) / "transactions.sqlite"
        initialize_database(self.database_path)
        register_user(100, "Pessoa A", self.database_path)
        register_user(200, "Pessoa B", self.database_path)

    def tearDown(self):
        self.tempdir.cleanup()

    def add_owned_player(self, user_id, name, overall=80, position="CA"):
        with connect(self.database_path) as connection:
            player_id = connection.execute(
                """
                INSERT INTO players (
                    name, game_position, overall, activity_status, phase2_candidate
                ) VALUES (?, ?, ?, 'active', 0)
                """,
                (name, position, overall),
            ).lastrowid
        self.assertTrue(claim_player(player_id, user_id, self.database_path))
        return player_id

    def set_memilets(self, user_id, amount):
        with connect(self.database_path) as connection:
            connection.execute(
                "UPDATE game_users SET memilets = ? WHERE discord_user_id = ?",
                (amount, str(user_id)),
            )

    def owner(self, player_id):
        with connect(self.database_path) as connection:
            row = connection.execute(
                "SELECT discord_user_id FROM player_ownership WHERE player_id = ?", (player_id,)
            ).fetchone()
        return row["discord_user_id"] if row else None

    def memilets(self, user_id):
        return get_wallet(user_id, self.database_path)["memilets"]


class SaleTests(DatabaseCase):
    def test_dismissal_refunds_market_value_and_releases_player(self):
        player_id = self.add_owned_player(100, "Liberado", overall=99)
        refund = dismiss_player(100, player_id, self.database_path)
        self.assertEqual(refund, sale_value(99))
        self.assertEqual(self.memilets(100), refund)
        self.assertIsNone(self.owner(player_id))

    def test_quote_matches_sale_and_shows_balance_and_warnings(self):
        ensure_team(100, "Pessoa A", self.database_path)
        starter = self.add_owned_player(100, "Titular", overall=85)
        reserve = self.add_owned_player(100, "Reserva", overall=70)
        set_lineup_slot(100, 11, starter, self.database_path)  # 4-3-3: vaga 11 é CA
        with connect(self.database_path) as connection:
            connection.execute(
                "UPDATE team_tactics SET captain_player_id = ?, penalty_player_id = ? "
                "WHERE discord_user_id = '100'",
                (starter, starter),
            )
            connection.execute(
                "INSERT INTO player_balls (player_id, balls) VALUES (?, 3)", (starter,)
            )
        self.set_memilets(100, 1000)
        quote = sale_quote(100, [starter, reserve], self.database_path)
        by_id = {player["id"]: player for player in quote["players"]}
        self.assertEqual(quote["total"], by_id[starter]["value"] + by_id[reserve]["value"])
        self.assertEqual((quote["balance"], quote["balance_after"]), (1000, 1000 + quote["total"]))
        warnings = " ".join(by_id[starter]["warnings"])
        self.assertIn("titular", warnings)
        self.assertIn("capitão", warnings)
        self.assertIn("pênalti", warnings)
        self.assertIn("3 bolas", warnings)
        self.assertEqual(by_id[reserve]["warnings"], [])
        self.assertEqual(sell_players(100, [starter, reserve], self.database_path), quote["total"])
        self.assertEqual(self.memilets(100), quote["balance_after"])
        self.assertEqual(get_lineup(100, self.database_path), [])

    def test_batch_sale_is_all_or_nothing(self):
        mine = self.add_owned_player(100, "Meu")
        other = self.add_owned_player(200, "De outro")
        with self.assertRaises(TransactionError):
            sell_players(100, [mine, other], self.database_path)
        self.assertEqual(self.owner(mine), "100")
        self.assertEqual(self.memilets(100), 0)
        with self.assertRaises(TransactionError):
            sale_quote(100, [], self.database_path)

    def test_card_value_follows_the_card_overall(self):
        self.assertEqual(sale_value(60), 400)
        values = [sale_value(overall) for overall in range(40, 101)]
        self.assertEqual(values, sorted(values))  # carta melhor sempre vale mais
        self.assertGreater(sale_value(90), 10_000)
        player_id = self.add_owned_player(100, "Valor", overall=99)
        self.assertEqual(
            market_values([player_id], self.database_path), {player_id: sale_value(99)}
        )

    def test_legend_without_market_value_is_worth_more_than_a_rich_89(self):
        with connect(self.database_path) as connection:
            legend = connection.execute(
                "INSERT INTO players (name, game_position, overall, overall_source, "
                "activity_status, phase2_candidate) VALUES ('Eusébio', 'CA', 97, 'Lenda', "
                "'active', 0)"
            ).lastrowid
            star = connection.execute(
                "INSERT INTO players (name, game_position, overall, market_value_eur, "
                "activity_status, phase2_candidate) VALUES ('Craque', 'CA', 89, 180000000, "
                "'active', 0)"
            ).lastrowid
        values = market_values([legend, star], self.database_path)
        self.assertEqual(values[legend], sale_value(97))
        self.assertGreater(values[legend], values[star] * 2)


class GiftTests(DatabaseCase):
    def test_gift_moves_player_and_clears_old_team_roles(self):
        ensure_team(100, "Pessoa A", self.database_path)
        player_id = self.add_owned_player(100, "Presente")
        set_lineup_slot(100, 11, player_id, self.database_path)
        given = give_players(100, "A", 200, "B", [player_id], 0, self.database_path)
        self.assertEqual([player["id"] for player in given], [player_id])
        self.assertEqual(self.owner(player_id), "200")
        self.assertEqual(get_lineup(100, self.database_path), [])

    def test_gift_memilets_needs_balance(self):
        self.set_memilets(100, 300)
        give_players(100, "A", 200, "B", [], 200, self.database_path)
        self.assertEqual((self.memilets(100), self.memilets(200)), (100, 200))
        with self.assertRaises(TransactionError):
            give_players(100, "A", 200, "B", [], 500, self.database_path)
        with self.assertRaises(TransactionError):
            give_players(100, "A", 100, "A", [], 10, self.database_path)
        with self.assertRaises(TransactionError):
            give_players(100, "A", 200, "B", [], 0, self.database_path)

    def test_cannot_give_someone_elses_player(self):
        other = self.add_owned_player(200, "Alheio")
        with self.assertRaises(TransactionError):
            give_players(100, "A", 200, "B", [other], 0, self.database_path)


class TradeTests(DatabaseCase):
    def test_multi_player_trade_transfers_everything_atomically(self):
        offered = [self.add_owned_player(100, f"Oferta {index}") for index in range(2)]
        requested = self.add_owned_player(200, "Pedido")
        self.set_memilets(100, 500)
        self.set_memilets(200, 300)
        trade_id = create_trade(
            100, "A", 200, "B", offered, [requested], 100, 50, self.database_path
        )
        details = trade_details(trade_id, self.database_path)
        self.assertEqual(len(details["proposer_players"]), 2)
        self.assertEqual(details["recipient_players"][0]["id"], requested)
        self.assertEqual(settle_trade(trade_id, 200, True, self.database_path), "accepted")
        self.assertEqual((self.memilets(100), self.memilets(200)), (450, 350))
        self.assertEqual({self.owner(player_id) for player_id in offered}, {"200"})
        self.assertEqual(self.owner(requested), "100")
        self.assertEqual(trade_details(trade_id, self.database_path)["status"], "accepted")

    def test_one_sided_trades_work_as_purchase_or_request(self):
        requested = self.add_owned_player(200, "Comprado")
        self.set_memilets(100, 1000)
        trade_id = create_trade(100, "A", 200, "B", [], [requested], 400, 0, self.database_path)
        settle_trade(trade_id, 200, True, self.database_path)
        self.assertEqual(self.owner(requested), "100")
        self.assertEqual((self.memilets(100), self.memilets(200)), (600, 400))
        with self.assertRaises(TransactionError):
            create_trade(100, "A", 200, "B", [], [], 0, 0, self.database_path)

    def test_trade_limits_and_ownership_are_checked(self):
        many = [self.add_owned_player(100, f"J{index}") for index in range(MAX_TRADE_PLAYERS + 1)]
        theirs = self.add_owned_player(200, "Deles")
        with self.assertRaises(TransactionError):
            create_trade(100, "A", 200, "B", many, [theirs], 0, 0, self.database_path)
        with self.assertRaises(TransactionError):
            create_trade(100, "A", 200, "B", [theirs], [], 0, 0, self.database_path)
        with self.assertRaises(TransactionError):
            create_trade(100, "A", 200, "B", [many[0]], [], 0, 999, self.database_path)

    def test_only_recipient_answers_and_proposer_can_cancel(self):
        offered = self.add_owned_player(100, "Oferta")
        requested = self.add_owned_player(200, "Pedido")
        trade_id = create_trade(
            100, "A", 200, "B", [offered], [requested], 0, 0, self.database_path
        )
        with self.assertRaises(TransactionError):
            settle_trade(trade_id, 100, True, self.database_path)
        with self.assertRaises(TransactionError):
            cancel_trade(trade_id, 200, self.database_path)
        cancel_trade(trade_id, 100, self.database_path)
        with self.assertRaises(TransactionError):
            settle_trade(trade_id, 200, True, self.database_path)
        self.assertEqual(self.owner(offered), "100")

    def test_trade_fails_without_partial_transfer_when_things_changed(self):
        offered = self.add_owned_player(100, "Oferta")
        requested = self.add_owned_player(200, "Pedido")
        self.set_memilets(100, 100)
        trade_id = create_trade(
            100, "A", 200, "B", [offered], [requested], 100, 0, self.database_path
        )
        self.set_memilets(100, 0)
        with self.assertRaises(TransactionError):
            settle_trade(trade_id, 200, True, self.database_path)
        self.assertEqual(self.owner(offered), "100")
        self.set_memilets(100, 100)
        sell_players(200, [requested], self.database_path)
        with self.assertRaises(TransactionError):
            settle_trade(trade_id, 200, True, self.database_path)
        self.assertEqual(self.owner(offered), "100")

    def test_expired_and_declined_trades(self):
        offered = self.add_owned_player(100, "Oferta")
        start = datetime(2026, 10, 22, 12, tzinfo=timezone.utc)
        trade_id = create_trade(
            100, "A", 200, "B", [offered], [], 0, 0, self.database_path, now=start
        )
        late = start + timedelta(minutes=16)
        self.assertEqual(settle_trade(trade_id, 200, True, self.database_path, now=late), "expired")
        trade_id = create_trade(100, "A", 200, "B", [offered], [], 0, 0, self.database_path)
        self.assertEqual(settle_trade(trade_id, 200, False, self.database_path), "declined")
        self.assertEqual(self.owner(offered), "100")


class TradeInterfaceTests(DatabaseCase, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        DatabaseCase.setUp(self)

    async def test_text_shortcut_prefills_builder_with_several_players(self):
        from src.cogs.trades import TradeBuilderView, TradesCog

        mine = [self.add_owned_player(100, name) for name in ("Vinicius", "Rodrygo")]
        theirs = self.add_owned_player(200, "Haaland")
        author = SimpleNamespace(id=100, display_name="A")
        target = SimpleNamespace(id=200, display_name="B", mention="<@200>")
        builder = TradeBuilderView(author, target, self.database_path)
        cog = TradesCog(SimpleNamespace(database_path=self.database_path))
        self.assertIsNone(cog._prefill(builder, "Vinicius, Rodrygo por Haaland 300 20"))
        self.assertEqual((builder.give, builder.take), (set(mine), {theirs}))
        self.assertEqual((builder.pay, builder.ask), (300, 20))
        embed = builder.embed()
        self.assertIn("Vinicius", embed.fields[0].value)
        self.assertIn("Haaland", embed.fields[1].value)
        self.assertIn("Não identifiquei", cog._prefill(builder, "Ninguém por Haaland"))

    async def test_sale_panel_lists_values_and_quotes_selection(self):
        from src.cogs.trades import SalePanelView

        ids = [self.add_owned_player(100, f"Reserva {n}", 60 + n) for n in range(3)]
        view = SalePanelView(100, self.database_path)
        select = view.children[2]  # raridade, posição e então os jogadores
        self.assertIn("vende por", select.options[0].description)
        view.selected = {ids[0], ids[1]}
        view._build()
        embed = view.embed()
        self.assertIn("Vender 2 jogadores", embed.title)
        self.assertIn("→", embed.fields[1].value)


if __name__ == "__main__":
    unittest.main()
