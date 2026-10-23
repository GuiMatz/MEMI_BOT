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
                        "topi",
                        "stats",
                        "img",
                        "semfoto",
                        "carteira",
                        "vender",
                        "proteger",
                        "dar",
                        "play",
                        "troca",
                        "pacote",
                        "shop",
                        "allin",
                        "ativar",
                        "inventario",
                        "time",
                        "personalizar",
                        "escudo",
                        "escalar",
                        "tatica",
                        "amistoso",
                        "campeonato",
                        "admin",
                        "tu",
                        "testeamistoso",
                        "testecampeonato",
                        "construir",
                        "destruir",
                        "renda",
                        "bilheteria",
                        "cosmeticos",
                        "emblema",
                        "uniforme",
                        "estadio",
                        "tecnico",
                        "liga",
                        "mascote",
                        "missoes",
                        "temporada",
                        "temporadas",
                        "rt",
                        "testeimagem",
                    },
                )
                self.assertIs(bot.get_command("ajudajogo"), bot.get_command("futae"))
                # mm!ajuda <comando>: aceita alias e mostra uso e exemplos.
                from src.command_help import COMMAND_HELP, command_help_embed

                embed = command_help_embed(bot, "sw")
                self.assertEqual(embed.title, "❔ mm!starwish")
                self.assertIsNone(command_help_embed(bot, "naoexiste"))
                for name in COMMAND_HELP:  # todo detalhe aponta para um comando de verdade
                    self.assertIsNotNone(bot.get_command(name), name)
            finally:
                await bot.close()
