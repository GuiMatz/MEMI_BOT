import random
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from src.cosmetics import CosmeticError, equip_key, grant
from src.db import connect, initialize_database
from src.economy import (
    CRATE_PITY_LIMIT,
    CRATE_PRICE,
    CratePrize,
    InsufficientFunds,
    get_crate_pity,
    open_crates,
)
from src.game import claim_player, get_wallet, register_user
from src.packs import PACK_PRICE, PackError, open_pack
from src.teams import ensure_team, get_team, set_lineup_slot
from src.transactions import (
    PACK_SALE_FACTOR,
    TransactionError,
    create_trade,
    give_players,
    quick_sale_ids,
    sale_quote,
    sale_value,
    sell_players,
    toggle_protection,
)


class Case(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.tempdir.name) / "market.sqlite"
        initialize_database(self.database_path)
        register_user(100, "Pessoa A", self.database_path)
        register_user(200, "Pessoa B", self.database_path)
        ensure_team(100, "Pessoa A", self.database_path)

    def tearDown(self):
        self.tempdir.cleanup()

    def add_player(self, name, overall=70, position="CA", owner=None):
        with connect(self.database_path) as connection:
            player_id = connection.execute(
                "INSERT INTO players (name, game_position, overall, activity_status, "
                "phase2_candidate) VALUES (?, ?, ?, 'active', 0)",
                (name, position, overall),
            ).lastrowid
        if owner is not None:
            self.assertTrue(claim_player(player_id, owner, self.database_path))
        return player_id

    def set_memilets(self, user_id, amount):
        with connect(self.database_path) as connection:
            connection.execute(
                "UPDATE game_users SET memilets = ? WHERE discord_user_id = ?",
                (amount, str(user_id)),
            )

    def memilets(self, user_id):
        return get_wallet(user_id, self.database_path)["memilets"]

    def mark_pack(self, player_id, until):
        with connect(self.database_path) as connection:
            connection.execute(
                "UPDATE player_ownership SET pack_origin = 1, pack_locked_until = ? "
                "WHERE player_id = ?",
                (until.isoformat(timespec="seconds"), player_id),
            )


class ProtectionAndQuickSaleTests(Case):
    def test_protected_player_cannot_be_sold_until_unlocked(self):
        player = self.add_player("Craque", 85, owner=100)
        self.assertTrue(toggle_protection(100, player, self.database_path))
        with self.assertRaises(TransactionError) as error:
            sell_players(100, [player], self.database_path)
        self.assertIn("protegido", str(error.exception))
        self.assertFalse(toggle_protection(100, player, self.database_path))
        self.assertGreater(sell_players(100, [player], self.database_path), 0)

    def test_protection_only_for_own_players(self):
        other = self.add_player("Alheio", owner=200)
        with self.assertRaises(TransactionError):
            toggle_protection(100, other, self.database_path)

    def test_quick_sale_filters_and_skips_starters_locked_and_pack_locked(self):
        # Overall bruto 45 vira 63 no jogo (Comum); 75 vira 81 (Raro).
        commons = [self.add_player(f"Comum {n}", 45, "ZAG", owner=100) for n in range(3)]
        rare = self.add_player("Raro", 75, "CA", owner=100)
        starter = self.add_player("Titular", 45, "GOL", owner=100)
        set_lineup_slot(100, 1, starter, self.database_path)
        toggle_protection(100, commons[0], self.database_path)
        self.mark_pack(commons[1], datetime.now(timezone.utc) + timedelta(days=3))
        self.assertEqual(quick_sale_ids(100, self.database_path, rarity="Comum"), [commons[2]])
        self.assertEqual(quick_sale_ids(100, self.database_path, below=70), [commons[2]])
        self.assertEqual(quick_sale_ids(100, self.database_path, position="CA"), [rare])
        with self.assertRaises(TransactionError):
            quick_sale_ids(100, self.database_path, rarity="Inexistente")

    def test_quick_sale_sells_more_than_25_at_once(self):
        ids = [self.add_player(f"Base {n}", 30, "ZAG", owner=100) for n in range(40)]
        found = quick_sale_ids(100, self.database_path, below=70)
        self.assertEqual(sorted(found), sorted(ids))
        total = sale_quote(100, found, self.database_path)["total"]
        self.assertEqual(sell_players(100, found, self.database_path), total)


class PackRuleTests(Case):
    def test_pack_player_sells_for_a_quarter_after_the_lock(self):
        player = self.add_player("Do pacote", 80, owner=100)
        self.mark_pack(player, datetime.now(timezone.utc) - timedelta(minutes=1))
        quote = sale_quote(100, [player], self.database_path)
        full = sale_value(80, None)
        self.assertEqual(quote["total"], max(1, round(full * PACK_SALE_FACTOR)))
        self.assertIn("veio de pacote", " ".join(quote["players"][0]["warnings"]))

    def test_pack_lock_blocks_sale_gift_and_trade(self):
        player = self.add_player("Travado", 80, owner=100)
        self.mark_pack(player, datetime.now(timezone.utc) + timedelta(days=6))
        with self.assertRaises(TransactionError):
            sell_players(100, [player], self.database_path)
        with self.assertRaises(TransactionError):
            give_players(100, "A", 200, "B", [player], 0, self.database_path)
        with self.assertRaises(TransactionError):
            create_trade(100, "A", 200, "B", [player], [], 0, 0, self.database_path)

    def test_pack_mark_follows_the_card_after_a_trade(self):
        player = self.add_player("Viajante", 80, owner=100)
        self.mark_pack(player, datetime.now(timezone.utc) - timedelta(minutes=1))
        give_players(100, "A", 200, "B", [player], 0, self.database_path)
        quote = sale_quote(200, [player], self.database_path)
        self.assertEqual(quote["total"], max(1, round(sale_value(80, None) * PACK_SALE_FACTOR)))


class OpenPackTests(Case):
    def test_pack_charges_and_gives_marked_player_never_base(self):
        for n in range(5):
            self.add_player(f"Base {n}", 20)  # 20 bruto vira 48 no jogo: Base
        good = self.add_player("Bom", 45)  # Comum
        self.set_memilets(100, PACK_PRICE + 10)
        now = datetime(2026, 10, 23, 12, tzinfo=timezone.utc)
        result = open_pack(100, "Pessoa A", self.database_path, now=now, rng=random.Random(1))
        self.assertEqual(result.player["id"], good)
        self.assertEqual(self.memilets(100), 10)
        self.assertEqual(result.locked_until, now + timedelta(days=7))
        with connect(self.database_path) as connection:
            row = connection.execute(
                "SELECT discord_user_id, pack_origin FROM player_ownership WHERE player_id = ?",
                (good,),
            ).fetchone()
        self.assertEqual((row["discord_user_id"], row["pack_origin"]), ("100", 1))

    def test_pack_needs_money_and_free_players(self):
        self.add_player("Bom", 45)
        with self.assertRaises(PackError):
            open_pack(100, "Pessoa A", self.database_path)
        self.set_memilets(100, PACK_PRICE * 3)
        open_pack(100, "Pessoa A", self.database_path, rng=random.Random(2))
        with self.assertRaises(PackError):
            open_pack(100, "Pessoa A", self.database_path, rng=random.Random(3))
        self.assertEqual(self.memilets(100), PACK_PRICE * 2)  # o pacote falho não cobra


class CrateBatchTests(Case):
    def test_open_several_crates_and_check_balance_first(self):
        self.add_player("Atacante")
        self.set_memilets(100, CRATE_PRICE * 5)
        results = open_crates(100, "Pessoa A", 5, self.database_path, rng=random.Random(4))
        self.assertEqual(len(results), 5)
        self.set_memilets(100, CRATE_PRICE * 3)
        with self.assertRaises(InsufficientFunds) as error:
            open_crates(100, "Pessoa A", 10, self.database_path)
        self.assertIn("paga 3", str(error.exception))
        with self.assertRaises(ValueError):
            open_crates(100, "Pessoa A", 7, self.database_path)
        self.assertLessEqual(get_crate_pity(100, self.database_path), CRATE_PITY_LIMIT)

    def test_prize_tiles_sum_coins_and_rolls_and_keep_items(self):
        from src.cogs.economy import EconomyCog

        cog = EconomyCog(SimpleNamespace(database_path=self.database_path))
        prizes = [
            CratePrize("basico", "memilets", 300),
            CratePrize("raro", "memilets", 900),
            CratePrize("basico", "instant_rolls", 2),
            CratePrize("basico", "instant_rolls", 1),
            CratePrize("epico", "star_wish_slot", 1),
        ]
        tiles = cog.prize_tiles(100, prizes)
        titles = [tile.title for tile in tiles]
        self.assertEqual(tiles[0].rarity, "epico")  # o mais raro aparece primeiro
        self.assertIn("1.200", titles)
        self.assertIn("3 rolls", titles)
        self.assertIn("+1 Star Wish", titles)


class CustomizeTests(Case, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        Case.setUp(self)

    def give_kit(self, name, path):
        with connect(self.database_path) as connection:
            grant(connection, "100", "uniforme", {"name": name, "image_path": path})

    def test_equip_key_only_equips_owned_items(self):
        self.give_kit("Uniforme Verde", "kits/verde.png")
        with self.assertRaises(CosmeticError):
            equip_key(100, "uniforme", "nao-tenho", self.database_path)
        from src.cosmetics import list_owned

        key = list_owned(100, "uniforme", self.database_path)[0]["key"]
        equip_key(100, "uniforme", key, self.database_path)
        self.assertEqual(get_team(100, self.database_path)["kit_path"], "kits/verde.png")

    async def test_panel_lists_palette_kinds_and_owned_items(self):
        from src.cogs.customize import PALETTE, CustomizeView

        self.give_kit("Uniforme Verde", "kits/verde.png")
        owner = SimpleNamespace(id=100, display_name="Pessoa A")
        panel = CustomizeView(owner, self.database_path)
        palette, kinds, items = panel.children[:3]
        self.assertEqual(len(palette.options), len(PALETTE))
        self.assertTrue(items.disabled)  # nada escolhido ainda
        panel.kind = "uniforme"
        panel._build()
        items = panel.children[2]
        self.assertEqual(
            [option.label for option in items.options], ["Nenhum (tirar)", "Uniforme Verde"]
        )
        panel.set_field("color", "#C0392B")
        embed = panel.embed()
        self.assertIn("#C0392B", embed.description)
        self.assertIn("monograma", embed.description)
        self.assertEqual(embed.color.value, 0xC0392B)


if __name__ == "__main__":
    unittest.main()
