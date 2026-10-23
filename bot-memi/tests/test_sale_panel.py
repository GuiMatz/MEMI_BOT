import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord

from src.db import connect, initialize_database
from src.game import claim_player, register_user
from src.teams import ensure_team, set_lineup_slot
from src.transactions import set_protection


def click(**data):
    """Interação falsa: guarda o que o painel mandou editar."""
    return SimpleNamespace(
        data=data,
        response=SimpleNamespace(edit_message=AsyncMock(), send_modal=AsyncMock()),
    )


class SalePanelTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.tempdir.name) / "panel.sqlite"
        initialize_database(self.database_path)
        register_user(100, "Pessoa", self.database_path)
        ensure_team(100, "Pessoa", self.database_path)

    def tearDown(self):
        self.tempdir.cleanup()

    def own(self, name, raw, position):
        with connect(self.database_path) as connection:
            player_id = connection.execute(
                "INSERT INTO players (name, game_position, overall, activity_status, "
                "phase2_candidate) VALUES (?, ?, ?, 'active', 0)",
                (name, position, raw),
            ).lastrowid
        claim_player(player_id, 100, self.database_path)
        return player_id

    def panel(self):
        from src.cogs.trades import SalePanelView

        return SalePanelView(100, self.database_path)

    def button(self, view, text):
        return next(
            item
            for item in view.children
            if isinstance(item, discord.ui.Button) and text in (item.label or "")
        )

    async def test_filter_by_rarity_and_select_all_passes_25(self):
        bases = [self.own(f"Base {n}", 20, "ZAG") for n in range(30)]  # 20 → 48: Base
        self.own("Bom", 75, "CA")  # Raro
        view = self.panel()
        await view._rarity(click(values=["0"]))
        self.assertEqual(len(view.filtered()), 30)
        await view._select_all(click())
        self.assertEqual(view.selected, set(bases))
        sell = self.button(view, "Vender 30")
        self.assertFalse(sell.disabled)
        self.assertIn("Vender 30 jogadores", view.embed().title)

    async def test_position_filter_and_starters_toggle(self):
        keeper = self.own("Goleiro", 60, "GOL")
        self.own("Zagueiro", 60, "ZAG")
        set_lineup_slot(100, 1, keeper, self.database_path)
        view = self.panel()
        self.assertNotIn(keeper, {int(p["id"]) for p in view.pool()})  # titular fica fora
        await view._toggle_starters(click())
        await view._position(click(values=["GOL"]))
        self.assertEqual([int(p["id"]) for p in view.filtered()], [keeper])

    async def test_protect_and_unprotect_from_the_panel(self):
        star = self.own("Craque", 85, "CA")
        reserve = self.own("Reserva", 50, "CA")
        view = self.panel()
        view.selected = {star}
        interaction = click()
        await view._lock(interaction)
        self.assertNotIn(star, {int(p["id"]) for p in view.pool()})
        sent = interaction.response.edit_message.call_args.kwargs["embed"]
        self.assertIn("protegido", sent.fields[-1].value)
        await view._to_protected(click())
        self.assertEqual([int(p["id"]) for p in view.pool()], [star])
        view.selected = {star}
        await view._unlock(click())
        await view._to_sell(click())
        self.assertEqual({int(p["id"]) for p in view.pool()}, {star, reserve})

    async def test_sell_button_sells_everything_selected(self):
        ids = [self.own(f"Base {n}", 20, "ZAG") for n in range(3)]
        set_protection(100, [ids[0]], True, self.database_path)
        view = self.panel()
        await view._select_all(click())
        self.assertEqual(view.selected, set(ids[1:]))  # o protegido nem aparece
        interaction = click()
        await view._sell(interaction)
        message = interaction.response.edit_message.call_args.kwargs["content"]
        self.assertIn("2 jogadores vendidos", message)
        with connect(self.database_path) as connection:
            left = connection.execute(
                "SELECT player_id FROM player_ownership WHERE discord_user_id = '100'"
            ).fetchall()
        self.assertEqual([row["player_id"] for row in left], [ids[0]])


if __name__ == "__main__":
    unittest.main()
