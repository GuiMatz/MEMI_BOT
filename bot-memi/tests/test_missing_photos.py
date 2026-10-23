import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from PIL import Image

from src.db import connect, initialize_database
from src.game import claim_player, register_user
from src.player_images import missing_photos


def picture():
    buffer = BytesIO()
    Image.new("RGB", (300, 400), (200, 40, 40)).save(buffer, format="PNG")
    return buffer.getvalue()


class MissingPhotoTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name) / "photos"
        (self.root / "players").mkdir(parents=True)
        self.database_path = Path(self.tempdir.name) / "missing.sqlite"
        initialize_database(self.database_path)
        register_user(100, "Pessoa", self.database_path)
        self.ids = {}
        with connect(self.database_path) as connection:
            for name, overall in (("Craque", 90), ("Bom", 80), ("Médio", 70), ("Fraco", 50)):
                self.ids[name] = connection.execute(
                    "INSERT INTO players (name, display_name, game_position, overall, "
                    "activity_status, phase2_candidate) VALUES (?, ?, 'CA', ?, 'active', 0)",
                    (name, name, overall),
                ).lastrowid
        (self.root / "players" / f"{self.ids['Bom']}.jpg").write_bytes(picture())
        claim_player(self.ids["Médio"], 100, self.database_path)

    def tearDown(self):
        self.tempdir.cleanup()

    def test_lists_players_without_photo_by_overall(self):
        missing, total = missing_photos(self.database_path, root=self.root)
        self.assertEqual(missing, [self.ids["Craque"], self.ids["Médio"], self.ids["Fraco"]])
        self.assertEqual(total, 4)
        mine, total = missing_photos(self.database_path, owner_id=100, root=self.root)
        self.assertEqual((mine, total), ([self.ids["Médio"]], 1))

    async def test_panel_upload_saves_and_moves_to_the_next(self):
        from src.cogs import game

        member = SimpleNamespace(
            id=100, display_name="Pessoa", guild_permissions=SimpleNamespace(manage_guild=False)
        )

        def scoped(*args, **kwargs):
            return missing_photos(*args, root=self.root, **kwargs)

        def add(player_id, data, by, path):
            from src.player_images import add_image

            return add_image(player_id, data, by, path, root=self.root)

        with patch.object(game, "missing_photos", scoped), patch.object(game, "add_image", add):
            view = game.MissingPhotosView(member, self.database_path)
            self.assertEqual(view.current()["id"], self.ids["Craque"])
            self.assertIn("1/4", view.page(view.current()).description)
            modal_holder = {}
            interaction = SimpleNamespace(
                response=SimpleNamespace(
                    send_modal=AsyncMock(side_effect=lambda m: modal_holder.update(modal=m))
                )
            )
            await view.upload.callback(interaction)
            submitted = SimpleNamespace(
                response=SimpleNamespace(is_done=lambda: False, defer=AsyncMock()),
                edit_original_response=AsyncMock(),
            )
            await modal_holder["modal"].on_upload(submitted, picture())
        self.assertEqual(view.current()["id"], self.ids["Médio"])  # passou para o próximo
        self.assertIn(
            "Craque",
            view.notice or submitted.edit_original_response.call_args.kwargs["embed"].description,
        )
        self.assertEqual(len(view.missing), 2)

    async def test_first_photo_is_free_but_changing_needs_the_owner(self):
        from src.cogs.game import ImageGalleryView, can_edit_photos

        stranger = SimpleNamespace(id=999, guild_permissions=SimpleNamespace(manage_guild=False))
        player = {"id": self.ids["Fraco"], "name": "Fraco", "overall": 60, "owner_id": None}
        self.assertFalse(can_edit_photos(stranger, player))
        gallery = ImageGalleryView(player, 999, False, self.database_path)
        self.assertFalse(gallery.add.disabled)  # sem nenhuma foto: qualquer um adiciona
        self.assertTrue(gallery.make_main.disabled)

    async def test_card_pager_has_photos_button(self):
        from src.cogs.common import CardPagerView

        opened = AsyncMock()
        view = CardPagerView([{"id": 1, "name": "X", "overall": 70}], on_photos=opened)
        button = next(item for item in view.children if getattr(item, "label", "") == "Fotos")
        await button.callback(SimpleNamespace())
        opened.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
