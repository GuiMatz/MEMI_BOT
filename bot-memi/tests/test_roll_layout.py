import unittest
from datetime import datetime, timezone

import discord

from src.accents import NEUTRAL, rarity_color
from src.cogs.common import card_embed
from src.cogs.rolls import RollLayout, rolls_line
from src.lineup_image import TIER_COLORS
from src.narration import match_accent
from src.rolls import RollOutcome

RESET = datetime(2026, 10, 22, 18, 0, tzinfo=timezone.utc)
PLAYER = {
    "id": 7,
    "name": "Vinícius Júnior",
    "display_name": "Vinícius Júnior",
    "overall": 88,
    "game_position": "PE",
    "club_name": "Real Madrid",
}


def outcome(remaining=5, kind="unowned", offer_id=1, wished_by=()):
    return RollOutcome(
        player=PLAYER,
        kind=kind,
        rolls_remaining=remaining,
        cycle_resets_at=RESET,
        offer_id=offer_id,
        reward_amount=120,
        wished_by=wished_by,
    )


def texts(layout):
    container = layout.children[0]
    return [item.content for item in container.children if isinstance(item, discord.ui.TextDisplay)]


class RollsLineTests(unittest.TestCase):
    def test_warning_only_with_two_rolls_left(self):
        self.assertEqual(rolls_line(2, RESET), "**⚠️ 2 ROLLS RESTANTES ⚠️**")
        self.assertIn("1 roll restante", rolls_line(1, RESET))
        self.assertIn("5 rolls restantes", rolls_line(5, RESET))
        self.assertIn("ACABARAM", rolls_line(0, RESET))
        self.assertIn("renovam às", rolls_line(0, RESET))


class RollLayoutTests(unittest.IsolatedAsyncioTestCase):
    async def test_card_frame_uses_rarity_color_and_warning_goes_below_card(self):
        layout = RollLayout(outcome(remaining=2), 88, "carta.png", ":memory:")
        container = layout.children[0]
        self.assertEqual(container.accent_colour, rarity_color(88))
        kinds = [type(item).__name__ for item in container.children]
        self.assertEqual(kinds, ["TextDisplay", "MediaGallery", "ActionRow", "TextDisplay"])
        self.assertEqual(texts(layout)[-1], "**⚠️ 2 ROLLS RESTANTES ⚠️**")
        self.assertIn("Vinícius Júnior", texts(layout)[0])

    async def test_wish_mention_and_claimed_state(self):
        layout = RollLayout(outcome(wished_by=("555",)), 88, "carta.png", ":memory:")
        self.assertIn("<@555>", texts(layout)[0])
        layout.status = "✅ Contratado por <@1>"
        layout._build()
        container = layout.children[0]
        self.assertFalse(any(isinstance(i, discord.ui.ActionRow) for i in container.children))
        self.assertIn("✅ Contratado por <@1>", texts(layout))

    async def test_own_duplicate_without_offer_has_no_button(self):
        result = outcome(kind="own_duplicate_ball", offer_id=None)
        layout = RollLayout(result, 88, "carta.png", ":memory:")
        container = layout.children[0]
        self.assertFalse(any(isinstance(i, discord.ui.ActionRow) for i in container.children))


class AccentTests(unittest.TestCase):
    def test_rarity_color_matches_card_tiers(self):
        self.assertEqual(rarity_color(88), discord.Color.from_rgb(*TIER_COLORS[4]))
        self.assertEqual(rarity_color(None), NEUTRAL)
        self.assertEqual(card_embed(PLAYER).color, rarity_color(88))

    def test_match_accent_follows_the_action(self):
        home, away = "#C0392B", "#2E86C1"

        def event(kind, side=None, home_goals=0, away_goals=0):
            return {"kind": kind, "side": side, "home_goals": home_goals, "away_goals": away_goals}

        live = [event("kickoff"), event("goal", "away", 0, 1)]
        self.assertEqual(match_accent(live, home, away), discord.Color.from_str(away))
        live.append(event("card", "home", 0, 1))  # cartão amarelo não muda a cor
        self.assertEqual(match_accent(live, home, away), discord.Color.from_str(away))
        live.append(event("halftime", None, 0, 1))
        self.assertEqual(match_accent(live, home, away), NEUTRAL)
        final = [event("goal", "home", 2, 1), event("fulltime", None, 2, 1)]
        self.assertEqual(
            match_accent(final, home, away, finished=True), discord.Color.from_str(home)
        )
        draw = [event("fulltime", None, 1, 1)]
        self.assertEqual(match_accent(draw, home, away, finished=True), NEUTRAL)
        shootout = draw + [event("penalty", "away", 3, 4)]
        self.assertEqual(
            match_accent(shootout, home, away, finished=True), discord.Color.from_str(away)
        )


if __name__ == "__main__":
    unittest.main()
