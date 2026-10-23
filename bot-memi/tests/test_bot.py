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
                        "pay",
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
                # ..roll, ..obter e ..rolls valem como mm!roll.
                from types import SimpleNamespace
                from unittest.mock import patch

                from src.bot import command_prefix

                with patch(
                    "src.bot.commands.when_mentioned", side_effect=lambda *_: []
                ):  # sem login
                    for text, prefix in (("..roll", ".."), ("MM!roll", "MM!"), ("mm!tu", "mm!")):
                        self.assertIn(prefix, command_prefix(bot, SimpleNamespace(content=text)))
                    self.assertEqual(command_prefix(bot, SimpleNamespace(content=".roll")), [])
                for alias in ("obter", "rolls"):
                    self.assertIs(bot.get_command(alias), bot.get_command("roll"))
                # /wish, /starwish, /scout, /vender e /proteger, com sugestões de nome.
                self.assertEqual(
                    {command.name for command in bot.tree.get_commands()},
                    {"wish", "starwish", "scout", "vender", "proteger"},
                )
            finally:
                await bot.close()
