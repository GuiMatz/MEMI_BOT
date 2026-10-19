import tempfile
import unittest
from pathlib import Path

from src.bot import create_bot


class DiscordBotSetupTests(unittest.IsolatedAsyncioTestCase):
    async def test_setup_loads_game_commands_and_message_content_intent(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            bot = create_bot(Path(temporary_directory) / "memi.sqlite")
            try:
                await bot.setup_hook()
                self.assertTrue(bot.intents.message_content)
                self.assertEqual(
                    {command.name for command in bot.commands},
                    {
                        "futae",
                        "help",
                        "scout",
                        "wishlist",
                        "left",
                        "top",
                        "roll",
                        "daily",
                        "upgrade",
                        "crates",
                        "wish",
                        "starwish",
                        "elenco",
                        "carteira",
                        "demitir",
                        "troca",
                        "time",
                        "escalar",
                        "tatica",
                        "amistoso",
                        "campeonato",
                        "admin",
                    },
                )
            finally:
                await bot.close()
