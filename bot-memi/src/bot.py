"""Inicialização do bot Discord do MEMI."""

from __future__ import annotations

import logging
import os
from pathlib import Path

import discord
from discord.ext import commands
from dotenv import load_dotenv

from src.db import DATABASE_PATH, ROOT, initialize_database, normalize_player_name

LOGGER = logging.getLogger("memi")
PREFIX = "mm!"
SHORT_PREFIX = ".."  # atalho: ..roll, ..amistoso = mm!roll, mm!amistoso


def command_prefix(bot: commands.Bot, message: discord.Message) -> list[str]:
    """Aceita `mm!` (em qualquer combinação de maiúsculas: MM!, Mm!…) e o atalho `..`."""
    prefixes = commands.when_mentioned(bot, message)
    for accepted in (PREFIX, SHORT_PREFIX):
        start = (message.content or "")[: len(accepted)]
        if start.casefold() == accepted:
            prefixes.append(start)
    return prefixes


class MemiBot(commands.Bot):
    def __init__(self, database_path: str | Path = DATABASE_PATH):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(
            command_prefix=command_prefix,
            case_insensitive=True,
            strip_after_prefix=True,
            help_command=None,
            intents=intents,
        )
        self.database_path = Path(database_path)

    async def on_ready(self) -> None:
        LOGGER.info(
            "Bot pronto: %s (%s); message_content=%s; comandos=%s",
            self.user,
            self.user.id if self.user else "desconhecido",
            self.intents.message_content,
            len(self.commands),
        )

    async def get_context(self, origin, *, cls=commands.Context):
        """Aceita o comando com ou sem acento (mm!tática = mm!tatica)."""
        context = await super().get_context(origin, cls=cls)
        if context.command is None and context.invoked_with:
            context.command = self.all_commands.get(
                normalize_player_name(context.invoked_with).replace(" ", "")
            )
        return context

    async def setup_hook(self) -> None:
        initialize_database(self.database_path)
        await self.load_extension("src.cogs.game")
        await self.load_extension("src.cogs.economy")
        await self.load_extension("src.cogs.rolls")
        await self.load_extension("src.cogs.trades")
        await self.load_extension("src.cogs.team")
        await self.load_extension("src.cogs.matches")
        await self.load_extension("src.cogs.club")
        await self.load_extension("src.cogs.admin")
        from src.encyclopedia import prepare_game_data

        report = prepare_game_data(self.database_path)
        LOGGER.info("Extensões do MEMI carregadas; dados sincronizados: %s", report)

    async def on_command_error(
        self,
        context: commands.Context,
        error: commands.CommandError,
    ) -> None:
        if isinstance(error, commands.CommandNotFound):
            return
        if isinstance(error, commands.MissingRequiredArgument):
            command_name = context.command.name if context.command else "comando"
            await context.reply(
                f"Falta um argumento. Use `mm!{command_name} {error.param.name}`.",
                mention_author=False,
            )
            return
        if isinstance(error, commands.BadArgument):
            await context.reply(
                "Não consegui entender esse argumento. Confira `mm!ajuda`.",
                mention_author=False,
            )
            return
        LOGGER.error(
            "Falha no comando %s",
            context.command.qualified_name if context.command else "desconhecido",
            exc_info=(type(error), error, error.__traceback__),
        )
        await context.reply(
            "Ocorreu um erro ao executar o comando. Tente novamente mais tarde.",
            mention_author=False,
        )


def create_bot(database_path: str | Path = DATABASE_PATH) -> MemiBot:
    """Cria uma instância pronta para conectar ao Discord."""
    return MemiBot(database_path)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    load_dotenv(ROOT / ".env")
    token = os.getenv("DISCORD_BOT_TOKEN")
    if not token:
        raise SystemExit("Configure DISCORD_BOT_TOKEN no arquivo .env antes de iniciar o bot.")
    bot = create_bot()
    try:
        bot.run(token, log_handler=None)
    except discord.LoginFailure as error:
        LOGGER.error("O Discord recusou o token configurado.")
        raise SystemExit(1) from error
    except discord.PrivilegedIntentsRequired as error:
        LOGGER.error("Ative Message Content Intent na página de configuração do bot Discord.")
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
