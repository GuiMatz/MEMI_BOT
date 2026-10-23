import asyncio
import tempfile
import unittest
from fractions import Fraction
from io import BytesIO
from math import comb
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from PIL import Image

from src.casino import (
    MAX_BET,
    MIN_BET,
    MINES_CELLS,
    MINES_OPTIONS,
    ROULETTE_BETS,
    CasinoError,
    MinesGame,
    credit,
    payout,
    place_bet,
    roulette_wins,
    slots_rtp,
)
from src.crate_animation import HOLD_MS
from src.db import connect, initialize_database
from src.game import get_wallet, register_user


class FairnessTests(unittest.TestCase):
    def test_every_game_returns_exactly_what_is_bet_on_average(self):
        self.assertEqual(slots_rtp(), 1)
        for bet, (_, multiplier) in ROULETTE_BETS.items():
            chosen = 7 if bet == "numero" else None
            wins = sum(roulette_wins(bet, number, chosen) for number in range(1, 37))
            self.assertEqual(Fraction(wins, 36) * multiplier, 1, bet)

    def test_mines_multiplier_is_the_exact_inverse_of_surviving(self):
        for mines in MINES_OPTIONS:
            for picks in range(1, MINES_CELLS - mines + 1):
                survive = Fraction(comb(MINES_CELLS - mines, picks), comb(MINES_CELLS, picks))
                game = MinesGame(mines, frozenset(range(mines)), list(range(mines, mines + picks)))
                self.assertEqual(game.multiplier * survive, 1)


class MoneyTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.tempdir.name) / "c.sqlite"
        initialize_database(self.database_path)
        register_user(1, "Ana", self.database_path)
        with connect(self.database_path) as connection:
            connection.execute("UPDATE game_users SET memilets = 5000 WHERE discord_user_id = '1'")

    def tearDown(self):
        self.tempdir.cleanup()

    def test_bet_limits_and_balance(self):
        with self.assertRaises(CasinoError):
            place_bet(1, "Ana", MIN_BET - 1, self.database_path)
        with self.assertRaises(CasinoError):
            place_bet(1, "Ana", MAX_BET + 1, self.database_path)
        with self.assertRaises(CasinoError):
            place_bet(1, "Ana", 6000, self.database_path)
        self.assertEqual(place_bet(1, "Ana", 1000, self.database_path), 4000)
        self.assertEqual(credit(1, 2000, self.database_path), 6000)

    def test_mines_cash_out_bomb_and_timeout(self):
        from src.cogs.casino import CasinoCog, MinesView

        cog = CasinoCog(SimpleNamespace(database_path=self.database_path))
        owner = SimpleNamespace(id=1, display_name="Ana")

        def interaction():
            return SimpleNamespace(
                user=owner, response=SimpleNamespace(edit_message=AsyncMock(), defer=AsyncMock())
            )

        async def run():
            # Saca depois de 2 casas seguras com 3 bombas.
            place_bet(1, "Ana", 1000, self.database_path)
            view = MinesView(cog, owner, 1000, MinesGame(3, frozenset({0, 1, 2})))
            await view._opener(5)(interaction())
            await view._opener(6)(interaction())
            await view._cash_out(interaction())
            expected = payout(1000, view.game.multiplier)
            self.assertEqual(get_wallet(1, self.database_path)["memilets"], 4000 + expected)
            # Bomba: perde a aposta.
            place_bet(1, "Ana", 1000, self.database_path)
            before = get_wallet(1, self.database_path)["memilets"]
            view = MinesView(cog, owner, 1000, MinesGame(3, frozenset({0, 1, 2})))
            await view._opener(0)(interaction())
            self.assertTrue(view.over)
            self.assertEqual(get_wallet(1, self.database_path)["memilets"], before)
            # Sumiu no meio: saca sozinho o que tinha.
            place_bet(1, "Ana", 1000, self.database_path)
            before = get_wallet(1, self.database_path)["memilets"]
            view = MinesView(cog, owner, 1000, MinesGame(1, frozenset({0})))
            await view._opener(3)(interaction())
            view.message = SimpleNamespace(edit=AsyncMock())
            await view.on_timeout()
            self.assertEqual(
                get_wallet(1, self.database_path)["memilets"],
                before + payout(1000, view.game.multiplier),
            )

        asyncio.run(run())

    def test_slots_round_charges_and_pays(self):
        from unittest.mock import patch

        from src.cogs import casino as casino_module
        from src.cogs.casino import CasinoCog

        cog = CasinoCog(SimpleNamespace(database_path=self.database_path))
        followup = SimpleNamespace(send=AsyncMock())
        interaction = SimpleNamespace(
            user=SimpleNamespace(id=1, display_name="Ana"),
            response=SimpleNamespace(defer=AsyncMock(), is_done=lambda: True),
            followup=followup,
        )
        with (
            patch.object(casino_module, "spin_slots", lambda rng: ("sete", "sete", "sete")),
            patch.object(casino_module, "slots_gif", lambda *a: b"GIF89a"),
        ):
            asyncio.run(cog.play_slots(interaction, 1000))
        self.assertEqual(get_wallet(1, self.database_path)["memilets"], 5000 - 1000 + 250_000)
        self.assertIn("Caça-níquel", followup.send.call_args.kwargs["embed"].title)


class HubTests(unittest.IsolatedAsyncioTestCase):
    async def test_hub_has_bet_controls_and_four_games(self):
        from src.cogs.casino import CasinoCog, CasinoView

        tempdir = tempfile.TemporaryDirectory()
        database_path = Path(tempdir.name) / "c.sqlite"
        initialize_database(database_path)
        register_user(1, "Ana", database_path)
        cog = CasinoCog(SimpleNamespace(database_path=database_path))
        view = CasinoView(cog, SimpleNamespace(id=1, display_name="Ana"), 1000)
        labels = [item.label for item in view.children]
        self.assertEqual(
            labels, ["½", "×2", "Valor…", "Cara ou coroa", "Caça-níquel", "Roleta", "Minas"]
        )
        self.assertIn("justos", view.embed().description)
        tempdir.cleanup()


class ArtTests(unittest.TestCase):
    def test_gifs_render_and_hold_on_the_result(self):
        from src.casino_art import coin_gif, roulette_gif, slots_gif

        for data in (
            coin_gif("cara", "CARA", True),
            slots_gif(("cereja", "cereja", "sino"), "1,24X", True),
            roulette_gif(17, "17 PRETO", False),
        ):
            image = Image.open(BytesIO(data))
            self.assertGreater(image.n_frames, 10)
            image.seek(image.n_frames - 1)
            self.assertGreaterEqual(image.info["duration"], HOLD_MS)


if __name__ == "__main__":
    unittest.main()
