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


class TeamCase(unittest.IsolatedAsyncioTestCase):
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


class TacticsPanelTests(TeamCase):
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


class TeamMenuTests(TeamCase):
    async def test_team_message_has_one_menu_with_every_action(self):
        from src.cogs.team import TEAM_ACTIONS, TeamLayout

        layout = TeamLayout(
            self.owner,
            get_team(100, self.database_path),
            get_lineup(100, self.database_path),
            self.database_path,
        )
        container = layout.children[0]
        rows = [item for item in container.children if isinstance(item, discord.ui.ActionRow)]
        self.assertEqual(len(rows), 1)
        (menu,) = rows[0].children
        self.assertIsInstance(menu, discord.ui.Select)
        self.assertEqual([option.value for option in menu.options], [a[0] for a in TEAM_ACTIONS])

    async def test_menu_opens_tactics_and_reports_failures(self):
        from src.cogs.team import TeamLayout

        layout = TeamLayout(
            self.owner,
            get_team(100, self.database_path),
            get_lineup(100, self.database_path),
            self.database_path,
        )
        interaction = click(values=["tactics"])
        interaction.user = SimpleNamespace(id=100)
        await layout._manage(interaction)
        sent = interaction.followup.send.call_args.kwargs
        self.assertIn("Tática", sent["embed"].title)
        roles = [item for item in sent["view"].children if isinstance(item, discord.ui.Select)][2]
        self.assertNotIn("©", "".join(str(option.emoji) for option in roles.options))

        broken = click(values=["tactics"])
        broken.user = SimpleNamespace(id=100)
        broken.followup.send = AsyncMock(side_effect=[RuntimeError("falhou"), None])
        broken.response.is_done = lambda: True
        with self.assertLogs("memi.team", level="ERROR"):
            await layout._manage(broken)
        self.assertEqual(broken.followup.send.call_args.kwargs, {"ephemeral": True})


if __name__ == "__main__":
    unittest.main()
