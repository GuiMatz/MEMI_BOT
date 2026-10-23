import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord

from src.db import connect, initialize_database
from src.game import claim_player, register_user
from src.teams import FORMATIONS, auto_fill_lineup, ensure_team, get_lineup, get_team


def click(**data):
    return SimpleNamespace(
        data=data,
        response=SimpleNamespace(defer=AsyncMock(), edit_message=AsyncMock()),
        edit_original_response=AsyncMock(),
        followup=SimpleNamespace(send=AsyncMock()),
    )


class TacticsPanelTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.tempdir.name) / "tactics.sqlite"
        initialize_database(self.database_path)
        register_user(100, "Pessoa", self.database_path)
        ensure_team(100, "Pessoa", self.database_path)
        for index, position in enumerate(FORMATIONS["4-3-3"]):
            with connect(self.database_path) as connection:
                player_id = connection.execute(
                    "INSERT INTO players (name, game_position, overall, activity_status, "
                    "phase2_candidate) VALUES (?, ?, 70, 'active', 0)",
                    (f"{position} {index}", position),
                ).lastrowid
            claim_player(player_id, 100, self.database_path)
        auto_fill_lineup(100, self.database_path)
        self.owner = SimpleNamespace(id=100, display_name="Pessoa")

    def tearDown(self):
        self.tempdir.cleanup()

    def panel(self):
        from src.cogs.team import TacticsView

        return TacticsView(self.owner, self.database_path)

    async def test_panel_has_menus_for_everything(self):
        view = self.panel()
        selects = [item for item in view.children if isinstance(item, discord.ui.Select)]
        self.assertEqual(len(selects), 4)  # formação, estilo, função e titular
        self.assertEqual(len(selects[0].options), len(FORMATIONS))
        self.assertEqual(len(selects[3].options), 11)
        embed = view.embed()
        self.assertEqual(embed.fields[0].value, "4-3-3")
        self.assertEqual(embed.image.url, "attachment://escalacao.png")

    async def test_style_and_roles_are_set_from_menus(self):
        view = self.panel()
        await view._style(click(values=["attack"]))
        self.assertEqual(get_team(100, self.database_path)["style"], "attack")
        striker = next(p for p in get_lineup(100, self.database_path) if p["position"] == "CA")
        await view._choose_role(click(values=["penalti"]))
        await view._choose_player(click(values=[str(striker["id"])]))
        self.assertEqual(get_team(100, self.database_path)["penalty_player_id"], striker["id"])

    async def test_incompatible_formation_offers_change_and_refill(self):
        view = self.panel()
        await view._formation(click(values=["5-4-1"]))
        self.assertEqual(get_team(100, self.database_path)["formation"], "4-3-3")
        self.assertEqual(view.pending_formation, "5-4-1")
        force = next(
            item
            for item in view.children
            if isinstance(item, discord.ui.Button) and "5-4-1" in (item.label or "")
        )
        await force.callback(click())
        self.assertEqual(get_team(100, self.database_path)["formation"], "5-4-1")
        self.assertEqual(len(get_lineup(100, self.database_path)), 11)
        self.assertIsNone(view.pending_formation)


if __name__ == "__main__":
    unittest.main()
