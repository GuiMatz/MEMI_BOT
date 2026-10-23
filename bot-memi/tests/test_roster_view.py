import unittest

from src.cogs.common import CardPagerView, card_embed
from src.cogs.game import ROSTER_SORTS, roster_line

PLAYERS = [
    {
        "id": 1,
        "name": "Zé Reserva",
        "overall": 70,
        "game_position": "CA",
        "lineup_position": None,
        "slot_order": None,
        "acquired_at": "2026-10-01",
    },
    {
        "id": 2,
        "name": "Ana Titular",
        "overall": 85,
        "game_position": "PE",
        "lineup_position": "PD",
        "slot_order": 10,
        "acquired_at": "2026-10-03",
    },
    {
        "id": 3,
        "name": "Bruno Goleiro",
        "overall": 80,
        "game_position": "GOL",
        "lineup_position": "GOL",
        "slot_order": 1,
        "acquired_at": "2026-10-02",
    },
]


class RosterViewTests(unittest.IsolatedAsyncioTestCase):
    def order(self, key):
        view = CardPagerView(list(PLAYERS), line_format=roster_line, sorts=ROSTER_SORTS)
        view.sort_key = key
        view._apply_sort()
        return [player["id"] for player in view.players]

    async def test_every_sort_option(self):
        self.assertEqual(self.order("overall"), [2, 3, 1])
        self.assertEqual(self.order("overall_asc"), [1, 3, 2])
        self.assertEqual(self.order("titulares"), [3, 2, 1])
        self.assertEqual(self.order("posicao"), [3, 2, 1])
        self.assertEqual(self.order("nome"), [2, 3, 1])
        self.assertEqual(self.order("recentes"), [2, 3, 1])
        self.assertEqual(self.order("antigos"), [1, 3, 2])

    async def test_starts_by_highest_overall_with_sort_menu(self):
        view = CardPagerView(
            list(PLAYERS), title="Elenco", line_format=roster_line, sorts=ROSTER_SORTS
        )
        self.assertEqual(view.players[0]["id"], 2)
        defaults = [option.value for option in view.sort_menu.options if option.default]
        self.assertEqual(defaults, ["overall"])
        self.assertIn("Overall: maior → menor", view.page().footer.text)

    async def test_starters_are_marked_in_list_and_card(self):
        lines = CardPagerView(list(PLAYERS), line_format=roster_line, sorts=ROSTER_SORTS).lines()
        self.assertIn("⭐ **85** — Ana Titular (PE›PD)", lines[0])
        self.assertIn("⭐ **80** — Bruno Goleiro (GOL)", lines[1])
        self.assertIn("▫️ **70** — Zé Reserva (CA)", lines[2])
        self.assertIn("✅ Titular como PD", card_embed(PLAYERS[1]).description)
        self.assertIn("✅ Titular\n", card_embed(PLAYERS[2]).description + "\n")
        self.assertNotIn("Titular", card_embed(PLAYERS[0]).description)

    async def test_plain_pager_still_works_without_sorts(self):
        view = CardPagerView(list(PLAYERS), list_lines=["a", "b"])
        self.assertEqual(view.lines(), ["a", "b"])
        self.assertIsNone(view.sort_key)


if __name__ == "__main__":
    unittest.main()
