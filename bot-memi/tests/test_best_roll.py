import random
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord

from src.cogs.rolls import RollLayout, RollsCog, best_roll
from src.db import connect, initialize_database
from src.game import register_user
from src.rolls import RollOutcome, claim_roll_offer, open_claim_offers, roll_player

NOW = datetime(2026, 10, 18, 12, 10, tzinfo=timezone.utc)
RESET = datetime(2026, 10, 18, 12, 30, tzinfo=timezone.utc)


def layout(overall, offer_id, remaining=1, resets_at=RESET, kind="unowned"):
    player = {"id": offer_id, "name": f"J{offer_id}", "display_name": f"J{offer_id}"}
    player.update(overall=overall, game_position="CA", club_name="Clube")
    result = RollOutcome(
        player=player,
        kind=kind,
        rolls_remaining=remaining,
        cycle_resets_at=resets_at,
        offer_id=offer_id,
    )
    return RollLayout(result, overall, "carta.png", ":memory:")


class OpenOffersTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.tempdir.name) / "memi.sqlite"
        initialize_database(self.database_path)
        register_user(100, "Roller", self.database_path)

    def tearDown(self):
        self.tempdir.cleanup()

    def roll(self, name):
        with connect(self.database_path) as connection:
            connection.execute("UPDATE players SET activity_status = 'inactive'")
            connection.execute(
                """
                INSERT INTO players (name, display_name, club_name, game_position, overall,
                                     activity_status, phase2_candidate)
                VALUES (?, ?, 'Clube', 'CA', 80, 'active', 0)
                """,
                (name, name),
            )
        return roll_player(100, "Roller", self.database_path, now=NOW, rng=random.Random(1))

    def test_only_pending_and_still_free_offers_count(self):
        claimed, free = self.roll("A"), self.roll("B")
        claim_roll_offer(claimed.offer_id, 100, self.database_path, now=NOW)
        ids = [claimed.offer_id, free.offer_id]
        self.assertEqual(open_claim_offers(ids, self.database_path, now=NOW), {free.offer_id})
        later = NOW + timedelta(minutes=6)  # a oferta dura 5 minutos
        self.assertEqual(open_claim_offers(ids, self.database_path, now=later), set())
        self.assertEqual(open_claim_offers([], self.database_path, now=NOW), set())


class BestRollTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.cog = RollsCog(SimpleNamespace(database_path=":memory:"))
        self.user = SimpleNamespace(id=1)

    def test_highest_overall_wins_and_tie_goes_to_the_latest(self):
        first, second, third = layout(80, 1), layout(85, 2), layout(85, 3)
        self.assertIs(best_roll([first, second, third]), third)
        self.assertIsNone(best_roll([]))

    def test_rolls_reset_with_the_cycle_and_skip_duplicates(self):
        self.cog.remember_roll(1, layout(80, 1))
        self.cog.remember_roll(1, layout(90, 2, kind="other_duplicate"))
        self.assertEqual(len(self.cog.remember_roll(1, layout(82, 3))), 2)
        next_cycle = RESET + timedelta(minutes=30)
        self.assertEqual(len(self.cog.remember_roll(1, layout(70, 4, resets_at=next_cycle))), 1)

    async def test_summary_offers_the_best_free_player_and_links_both_messages(self):
        rolls = [layout(80, 1), layout(88, 2), layout(84, 3, remaining=0)]
        for roll in rolls:
            roll.message = AsyncMock()
        send = AsyncMock(return_value=AsyncMock())
        image = discord.File(__file__, filename="carta.png")
        with (
            patch("src.cogs.rolls.open_claim_offers", return_value={1, 2, 3}),
            patch("src.cogs.rolls.card_file", AsyncMock(return_value=image)),
        ):
            await self.cog.send_best_roll(self.user, send, rolls)
        summary = send.call_args.kwargs["view"]
        self.assertIs(summary.result, rolls[1].result)
        self.assertIn("Melhor da rodada", summary.children[0].children[0].content)
        self.assertIs(rolls[1].twin, summary)
        # Contratou pelo resumo: o roll original também fecha.
        summary.status = "✅ Contratado"
        await summary._close_twin()
        self.assertEqual(rolls[1].status, "✅ Contratado")
        rolls[1].message.edit.assert_awaited()

    async def test_no_summary_when_the_best_is_gone_or_there_was_one_roll(self):
        send = AsyncMock()
        with patch("src.cogs.rolls.open_claim_offers", return_value=set()):
            await self.cog.send_best_roll(self.user, send, [layout(80, 1), layout(88, 2)])
        await self.cog.send_best_roll(self.user, send, [layout(80, 1)])
        send.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
