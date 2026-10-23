import asyncio
import random
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from src.db import connect, initialize_database
from src.game import get_wallet, register_user
from src.penalty_game import ROUNDS, Shootout
from src.wagers import WagerError, parse_wager, pay, take_stakes


class Fixed(random.Random):
    def __init__(self, value):
        super().__init__(0)
        self.value = value

    def random(self):
        return self.value


class ShootoutRuleTests(unittest.TestCase):
    def test_guessing_the_corner_usually_saves_and_wrong_side_is_a_goal(self):
        game = Shootout((1, 2))
        self.assertTrue(game.kick("esquerda", "direita", Fixed(0.5)).goal)  # lado errado
        self.assertFalse(game.kick("meio", "meio", Fixed(0.1)).goal)  # acertou: defende
        self.assertTrue(game.kick("meio", "meio", Fixed(0.9)).goal)  # acertou, mas entrou
        post = game.kick("direita", "esquerda", Fixed(0.01))
        self.assertTrue(post.post and not post.goal)  # na trave

    def test_ends_early_when_one_side_cannot_catch_up(self):
        game = Shootout((1, 2))
        for _ in range(3):
            game.kick("esquerda", "direita", Fixed(0.5))  # lado 0 marca
            game.kick("esquerda", "esquerda", Fixed(0.1))  # lado 1 perde
        self.assertEqual(game.score(), (3, 0))
        self.assertTrue(game.finished)
        self.assertEqual(game.winner, 0)

    def test_tie_after_five_goes_to_sudden_death(self):
        game = Shootout((1, 2))
        for _ in range(ROUNDS * 2):
            game.kick("esquerda", "direita", Fixed(0.5))
        self.assertEqual(game.score(), (5, 5))
        self.assertFalse(game.finished)
        game.kick("esquerda", "direita", Fixed(0.5))  # 6 x 5, mas o outro ainda bate
        self.assertFalse(game.finished)
        game.kick("esquerda", "esquerda", Fixed(0.1))
        self.assertEqual(game.winner, 0)


class WagerTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.tempdir.name) / "w.sqlite"
        initialize_database(self.database_path)
        for user_id in (1, 2):
            register_user(user_id, f"P{user_id}", self.database_path)

    def tearDown(self):
        self.tempdir.cleanup()

    def balance(self, user_id, amount=None):
        if amount is not None:
            with connect(self.database_path) as connection:
                connection.execute(
                    "UPDATE game_users SET memilets = ? WHERE discord_user_id = ?",
                    (amount, str(user_id)),
                )
        return get_wallet(user_id, self.database_path)["memilets"]

    def test_parse(self):
        self.assertEqual(
            [parse_wager(t) for t in ("1000", "1.000", "2k", "abc")], [1000, 1000, 2000, None]
        )

    def test_both_pay_or_nobody_pays_and_winner_takes_the_pot(self):
        self.balance(1, 5_000)
        self.balance(2, 500)
        with self.assertRaises(WagerError) as error:
            take_stakes((1, "Ana"), (2, "Bia"), 1_000, self.database_path)
        self.assertIn("Bia", str(error.exception))
        self.assertEqual((self.balance(1), self.balance(2)), (5_000, 500))  # ninguém pagou
        self.balance(2, 1_000)
        take_stakes((1, "Ana"), (2, "Bia"), 1_000, self.database_path)
        pay(2, 2_000, self.database_path)
        self.assertEqual((self.balance(1), self.balance(2)), (4_000, 2_000))
        with self.assertRaises(WagerError):
            take_stakes((1, "Ana"), (2, "Bia"), 10, self.database_path)  # abaixo do mínimo


class PenaltyFlowTests(unittest.TestCase):
    def test_full_shootout_through_the_buttons_pays_the_winner(self):
        from src.cogs.penalties import KickView, PenaltyMatch

        tempdir = tempfile.TemporaryDirectory()
        database_path = Path(tempdir.name) / "p.sqlite"
        initialize_database(database_path)
        for user_id in (1, 2):
            register_user(user_id, f"P{user_id}", database_path)
        message = SimpleNamespace(edit=AsyncMock())

        async def run():
            match = PenaltyMatch((1, 2), ("Ana", "Bia"), 1_000, database_path, message, Fixed(0.1))
            await match.start()
            # Ana sempre bate no canto certo; Bia sempre é defendida.
            while not match.game.finished:
                view = message.edit.call_args.kwargs["view"]
                self.assertIsInstance(view, KickView)
                shooter = match.players[match.game.shooter]
                keeper = match.players[match.game.keeper]
                ana_shoots = shooter == 1
                for user_id, corner in (
                    (shooter, "esquerda"),
                    (keeper, "direita" if ana_shoots else "esquerda"),
                ):
                    interaction = SimpleNamespace(
                        user=SimpleNamespace(id=user_id),
                        response=SimpleNamespace(send_message=AsyncMock()),
                    )
                    await view._chooser(corner)(interaction)
            return match

        match = asyncio.run(run())
        self.assertEqual(match.game.winner, 0)
        self.assertEqual(match.game.score(), (3, 0))
        self.assertIsNone(message.edit.call_args.kwargs["view"])
        self.assertIn("Ana** venceu", message.edit.call_args.kwargs["embed"].description)
        self.assertEqual(
            get_wallet(1, database_path)["memilets"] - get_wallet(2, database_path)["memilets"],
            2_000,
        )
        tempdir.cleanup()


if __name__ == "__main__":
    unittest.main()
