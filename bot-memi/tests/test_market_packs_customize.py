import random
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

from src.cosmetics import CosmeticError, equip_key, grant
from src.db import connect, initialize_database
from src.economy import (
    CRATE_EXTRA_CHANCES,
    CRATE_PITY_LIMIT,
    CRATE_PRICE,
    EPIC_CRATE_EXTRA_CHANCES,
    EPIC_CRATE_MULTIPLIER,
    CratePrize,
    InsufficientFunds,
    get_crate_pity,
    open_crates,
)
from src.game import claim_player, get_wallet, register_user
from src.ratings import game_overall
from src.packs import (
    ALL_IN_PRICE,
    ALL_IN_TOP,
    ALL_IN_WHEEL,
    EPIC_PACK_ODDS,
    EPIC_PACK_PRICE,
    PACK_PRICE,
    PackError,
    open_all_in,
    open_pack,
)
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

    def test_quick_sale_filters_skips_starters_and_locked_but_sells_pack_cards(self):
        # Overall bruto 45 vira 63 no jogo (Comum); 75 vira 81 (Raro).
        commons = [self.add_player(f"Comum {n}", 45, "ZAG", owner=100) for n in range(3)]
        rare = self.add_player("Raro", 75, "CA", owner=100)
        starter = self.add_player("Titular", 45, "GOL", owner=100)
        set_lineup_slot(100, 1, starter, self.database_path)
        toggle_protection(100, commons[0], self.database_path)
        self.mark_pack(commons[1], datetime.now(timezone.utc) + timedelta(days=3))
        # Carta de pacote entra na venda (por 50%); protegido e titular ficam de fora.
        self.assertEqual(
            quick_sale_ids(100, self.database_path, rarity="Comum"), [commons[1], commons[2]]
        )
        self.assertEqual(
            quick_sale_ids(100, self.database_path, below=70), [commons[1], commons[2]]
        )
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
        full = sale_value(game_overall(80))
        self.assertEqual(quote["total"], max(1, round(full * PACK_SALE_FACTOR)))
        self.assertIn("veio de pacote", " ".join(quote["players"][0]["warnings"]))

    def test_pack_card_sells_right_away_at_half_but_cannot_be_given_or_traded(self):
        player = self.add_player("Travado", 80, owner=100)
        self.mark_pack(player, datetime.now(timezone.utc) + timedelta(days=6))
        with self.assertRaises(TransactionError):
            give_players(100, "A", 200, "B", [player], 0, self.database_path)
        with self.assertRaises(TransactionError):
            create_trade(100, "A", 200, "B", [player], [], 0, 0, self.database_path)
        expected = max(1, round(sale_value(game_overall(80)) * PACK_SALE_FACTOR))
        self.assertEqual(PACK_SALE_FACTOR, 0.5)
        self.assertEqual(sell_players(100, [player], self.database_path), expected)

    def test_pack_mark_follows_the_card_after_a_trade(self):
        player = self.add_player("Viajante", 80, owner=100)
        self.mark_pack(player, datetime.now(timezone.utc) - timedelta(minutes=1))
        give_players(100, "A", 200, "B", [player], 0, self.database_path)
        quote = sale_quote(200, [player], self.database_path)
        self.assertEqual(
            quote["total"], max(1, round(sale_value(game_overall(80)) * PACK_SALE_FACTOR))
        )


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

    def test_epic_pack_costs_more_and_only_gives_rare_or_better(self):
        self.add_player("Comum", 45)  # 63 no jogo: o épico não pode cair nele
        rare = self.add_player("Raro", 75)  # 81 no jogo
        self.set_memilets(100, EPIC_PACK_PRICE * 2)
        result = open_pack(100, "Pessoa A", self.database_path, rng=random.Random(1), epic=True)
        self.assertEqual(result.player["id"], rare)
        self.assertEqual(result.price, EPIC_PACK_PRICE)
        self.assertEqual(self.memilets(100), EPIC_PACK_PRICE)
        with self.assertRaises(PackError):
            open_pack(100, "Pessoa A", self.database_path, rng=random.Random(2), epic=True)
        self.assertEqual(self.memilets(100), EPIC_PACK_PRICE)  # sem Raro livre, não cobra
        self.assertGreater(EPIC_PACK_PRICE, PACK_PRICE * 3)
        self.assertEqual(min(EPIC_PACK_ODDS), 3)
        self.assertAlmostEqual(sum(EPIC_PACK_ODDS.values()), 1.0)

    def test_epic_pack_instant_sale_stays_below_its_price(self):
        from src.ratings import RARITY_TIERS

        expected = 0.0
        for tier, chance in EPIC_PACK_ODDS.items():
            low, high = RARITY_TIERS[tier][2], RARITY_TIERS[tier][3]
            values = [sale_value(overall) * PACK_SALE_FACTOR for overall in range(low, high + 1)]
            expected += chance * sum(values) / len(values)
        self.assertLess(expected, EPIC_PACK_PRICE)


class FixedWheel(random.Random):
    """Para a roleta na fatia `slot`; o resto do sorteio pega sempre o primeiro."""

    def __init__(self, slot):
        super().__init__(0)
        self.slot = slot

    def randrange(self, n):
        return self.slot if n == len(ALL_IN_WHEEL) else 0

    def choices(self, population, weights=None, k=1):
        return [population[0]] * k


class AllInTests(Case):
    def test_wheel_is_one_in_ten_for_icon_or_better(self):
        gold = sum(1 for tier in ALL_IN_WHEEL if tier in ALL_IN_TOP)
        self.assertEqual(gold / len(ALL_IN_WHEEL), 0.1)
        self.assertEqual(min(ALL_IN_TOP), 6)  # 6 = Ícone

    def test_without_a_free_icon_the_wheel_does_not_spin_or_charge(self):
        self.add_player("Comum", 45)
        self.set_memilets(100, ALL_IN_PRICE)
        with self.assertRaises(PackError) as error:
            open_all_in(100, "Pessoa A", self.database_path, rng=FixedWheel(0))
        self.assertIn("Nada foi cobrado", str(error.exception))
        self.assertEqual(self.memilets(100), ALL_IN_PRICE)

    def test_gold_slice_gives_an_icon_and_other_slices_a_common_card(self):
        common = self.add_player("Comum", 45)
        icon = self.add_player("Ícone", 96)
        self.set_memilets(100, ALL_IN_PRICE * 2)
        gold = ALL_IN_WHEEL.index(6)
        result = open_all_in(100, "Pessoa A", self.database_path, rng=FixedWheel(gold))
        self.assertEqual(
            (result.player["id"], result.slot, result.price), (icon, gold, ALL_IN_PRICE)
        )
        self.add_player("Outro Ícone", 95)  # precisa haver Ícone livre para girar
        result = open_all_in(100, "Pessoa A", self.database_path, rng=FixedWheel(0))
        self.assertEqual(result.player["id"], common)
        self.assertEqual(self.memilets(100), 0)

    def test_wheel_gif_spins_and_holds_on_the_last_frame(self):
        from PIL import Image

        from src.crate_animation import HOLD_MS, all_in_wheel_gif
        from src.lineup_image import TIER_COLORS

        data = all_in_wheel_gif(
            ALL_IN_WHEEL, 5, TIER_COLORS, {i: str(i) for i in range(9)}, top_label="ÍCONE"
        )
        image = Image.open(BytesIO(data))
        self.assertGreater(image.n_frames, 30)
        image.seek(image.n_frames - 1)
        self.assertGreaterEqual(image.info["duration"], HOLD_MS)  # o Pillow junta quadros iguais

    def test_spin_then_the_message_becomes_the_card(self):
        import asyncio
        from unittest.mock import AsyncMock, patch

        import discord

        from src.cogs import economy as cog_module
        from src.cogs.economy import EconomyCog

        self.add_player("Ícone", 96)
        self.add_player("Comum", 45)
        self.set_memilets(100, ALL_IN_PRICE)
        cog = EconomyCog(SimpleNamespace(database_path=self.database_path))
        author = SimpleNamespace(id=100, display_name="Pessoa A")
        card = (discord.Embed(title="carta"), b"GIF89a")
        send, edit = AsyncMock(), AsyncMock()
        with (
            patch.object(cog_module, "all_in_wheel_gif", lambda *a, **k: b"GIF89a"),
            patch.object(cog_module, "ALL_IN_SPIN_SECONDS", 0),
            patch.object(EconomyCog, "card_reveal", AsyncMock(return_value=card)),
        ):
            asyncio.run(cog.all_in_with_animation(author, send, edit))
        self.assertEqual(send.call_args.kwargs["file"].filename, "roleta.gif")
        self.assertEqual(edit.call_args.kwargs["embed"].title, "carta")


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

    def test_batch_lists_every_crate_and_counts_rarities_for_the_gif(self):
        import asyncio
        from unittest.mock import AsyncMock, patch

        from src.cogs import economy as cog_module
        from src.cogs.economy import EconomyCog

        self.add_player("Atacante")
        self.set_memilets(100, CRATE_PRICE * 10)
        cog = EconomyCog(SimpleNamespace(database_path=self.database_path))
        author = SimpleNamespace(id=100, display_name="Pessoa A")
        seen = {}

        def fake_gif(rarity, tiles, counts=None, headline=None):
            seen.update(rarity=rarity, tiles=tiles, counts=dict(counts or {}))
            return b"GIF89a"

        edit = AsyncMock()
        with patch.object(cog_module, "prize_gif", fake_gif):
            asyncio.run(cog.open_with_animation(author, AsyncMock(), edit, count=10))
        embed = edit.call_args.kwargs["embed"]
        self.assertEqual(sum(seen["counts"].values()), 10)
        # Os cartões do GIF somam exatamente o que o texto diz no topo.
        coins = next((t.title for t in seen["tiles"] if t.icon == "coin"), None)
        if coins:
            self.assertIn(coins, embed.description)
        caixas = [field for field in embed.fields if field.name.startswith("Caixa ")]
        self.assertEqual(len(caixas), 10)
        for rarity, amount in seen["counts"].items():
            label = cog_module.RARITY_LABELS[rarity]
            self.assertEqual(sum(field.name.endswith(label) for field in caixas), amount)

    def test_single_crate_gif_shows_exactly_its_prizes(self):
        from src.cogs.economy import EconomyCog

        self.add_player("Atacante")
        self.set_memilets(100, CRATE_PRICE)
        cog = EconomyCog(SimpleNamespace(database_path=self.database_path))
        (result,) = open_crates(100, "Pessoa A", 1, self.database_path, rng=random.Random(9))
        tiles = cog.prize_tiles(100, list(result.prizes))
        coins = sum(prize.amount for prize in result.prizes if prize.kind == "memilets")
        rolls = sum(prize.amount for prize in result.prizes if prize.kind == "instant_rolls")
        titles = [tile.title for tile in tiles]
        if coins:
            self.assertIn(f"{coins:,}".replace(",", "."), titles)
        if rolls:
            self.assertIn(f"{rolls} roll{'s' if rolls > 1 else ''}", titles)

    def test_epic_crate_never_comes_with_only_the_basic_item(self):
        self.add_player("Atacante")
        self.set_memilets(100, CRATE_PRICE * EPIC_CRATE_MULTIPLIER * 10)
        results = open_crates(
            100, "Pessoa A", 10, self.database_path, rng=random.Random(5), epic=True
        )
        # A primeira custa 5 caixas; o prêmio "-10 no preço da crate" barateia as seguintes.
        self.assertEqual(results[0].price, CRATE_PRICE * EPIC_CRATE_MULTIPLIER)
        self.assertTrue(
            all(result.price <= CRATE_PRICE * EPIC_CRATE_MULTIPLIER for result in results)
        )
        self.assertTrue(all(result.best_rarity != "basico" for result in results))
        self.assertAlmostEqual(sum(EPIC_CRATE_EXTRA_CHANCES.values()), 1.0)
        # Mesmas proporções da caixa comum, só sem a chance de vir só o básico.
        total = sum(CRATE_EXTRA_CHANCES.values())
        for rarity, chance in EPIC_CRATE_EXTRA_CHANCES.items():
            self.assertAlmostEqual(chance, CRATE_EXTRA_CHANCES[rarity] / total)
        self.set_memilets(100, 0)
        with self.assertRaises(InsufficientFunds) as error:
            open_crates(100, "Pessoa A", 1, self.database_path, epic=True)
        self.assertIn("épica", str(error.exception))

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


class InventoryTests(Case, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        Case.setUp(self)

    def give(self, item, amount=1):
        from src.inventory import give

        with connect(self.database_path) as connection:
            give(connection, "100", item, amount)

    def test_crate_can_give_crates_and_packs_that_go_to_the_inventory(self):
        from unittest.mock import patch

        from src import economy
        from src.inventory import get_inventory

        self.assertIn("player_pack", economy.CRATE_ITEMS["raro"])
        self.assertIn("player_pack", economy.CRATE_ITEMS["epico"])
        self.assertIn("epic_pack", economy.CRATE_ITEMS["lendario"])
        for rarity, item, stored in (
            ("raro", "bonus_crate", "caixa"),
            ("raro", "player_pack", "pacote"),
            ("epico", "epic_crate", "caixa_epica"),
            ("lendario", "epic_pack", "pacote_epico"),
        ):
            with (
                patch.dict(economy.CRATE_ITEMS, {rarity: (item,)}),
                connect(self.database_path) as connection,
            ):
                prize = economy._give_item(
                    connection, "100", rarity, random.Random(1), self.database_path
                )
            self.assertEqual((prize.kind, prize.rarity), (item, rarity))
            self.assertIn("mm!inventario", economy.describe_prize(prize))
        self.assertEqual(
            get_inventory(100, self.database_path),
            {"caixa": 1, "caixa_epica": 1, "pacote": 1, "pacote_epico": 1},
        )

    def test_inventory_crate_opens_for_free_and_only_once(self):
        from src.inventory import get_inventory

        self.add_player("Atacante")
        self.set_memilets(100, 0)
        self.give("caixa")
        (result,) = open_crates(100, "Pessoa A", 1, self.database_path, from_inventory=True)
        self.assertEqual(result.price, 0)
        self.assertEqual(get_inventory(100, self.database_path), {})
        with self.assertRaises(InsufficientFunds):
            open_crates(100, "Pessoa A", 1, self.database_path, from_inventory=True)

    def test_inventory_pack_is_free_and_comes_back_if_the_market_is_empty(self):
        from src.inventory import get_inventory

        self.set_memilets(100, 0)
        self.give("pacote")
        with self.assertRaises(PackError):
            open_pack(100, "Pessoa A", self.database_path, from_inventory=True)
        self.assertEqual(get_inventory(100, self.database_path), {"pacote": 1})
        player = self.add_player("Bom", 45)
        result = open_pack(
            100, "Pessoa A", self.database_path, rng=random.Random(1), from_inventory=True
        )
        self.assertEqual((result.player["id"], result.price), (player, 0))
        self.assertEqual(self.memilets(100), 0)
        self.assertEqual(get_inventory(100, self.database_path), {})

    async def test_inventory_menu_has_one_open_button_per_item(self):
        from src.cogs.economy import EconomyCog

        cog = EconomyCog(SimpleNamespace(database_path=self.database_path))
        author = SimpleNamespace(id=100, display_name="Pessoa A")
        embed, view = cog.inventory_menu(author)
        self.assertIn("Vazio", embed.description)
        self.assertEqual(view.children, [])
        self.give("caixa_epica", 2)
        self.give("pacote")
        embed, view = cog.inventory_menu(author)
        self.assertIn("Caixa épica** × 2", embed.description)
        self.assertEqual(
            [item.label for item in view.children],
            ["Abrir caixa épica (2)", "Abrir pacote de jogador (1)"],
        )


class ShopTests(Case, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        Case.setUp(self)

    async def test_shop_lists_everything_for_sale_with_buy_buttons(self):
        from src.cogs.economy import EconomyCog

        cog = EconomyCog(SimpleNamespace(database_path=self.database_path))
        author = SimpleNamespace(id=100, display_name="Pessoa A")
        embed, view = cog.shop_menu(author)
        names = [field.name for field in embed.fields]
        for section in ("📦 Caixas", "🃏 Pacotes de jogador", "🛒 Upgrades", "🏗️ Torre"):
            self.assertIn(section, names)
        text = " ".join(field.value for field in embed.fields)
        self.assertIn("Caixa épica", text)
        self.assertIn("Pacote épico", text)
        labels = [item.label for item in view.children]
        self.assertIn(
            f"Caixa épica ({CRATE_PRICE * EPIC_CRATE_MULTIPLIER:,})".replace(",", "."), labels
        )
        self.assertIn(f"Pacote épico ({EPIC_PACK_PRICE:,})".replace(",", "."), labels)
        self.assertIn("Upgrades", labels)

    def test_loja_opens_the_shop_not_the_upgrades(self):
        from src.cogs.economy import EconomyCog

        self.assertIn("loja", EconomyCog.shop.aliases)
        self.assertNotIn("loja", EconomyCog.upgrade.aliases)


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
