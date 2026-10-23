"""Apostas entre duas pessoas (amistoso e pênaltis apostados).

As duas entram com o mesmo valor quando a partida é aceita (`take_stakes`, numa transação
só: ou as duas pagam ou ninguém paga) e quem vence leva o pote inteiro (`pay`).
"""

from __future__ import annotations

from pathlib import Path

from src.db import DATABASE_PATH, connect
from src.game import register_user

MIN_WAGER = 100
MAX_WAGER = 1_000_000


class WagerError(Exception):
    """Aposta recusada (valor ou saldo); a mensagem vai para as pessoas."""


def parse_wager(text: str) -> int | None:
    """Converte "1000", "1.000" ou "1k" em 1000; None se não é um valor."""
    cleaned = text.strip().lower().replace(".", "").replace(",", "")
    multiplier = 1
    if cleaned.endswith("k"):
        cleaned, multiplier = cleaned[:-1], 1_000
    return int(cleaned) * multiplier if cleaned.isdigit() else None


def check_wager(amount: int) -> None:
    if not MIN_WAGER <= amount <= MAX_WAGER:
        raise WagerError(
            f"A aposta vai de {MIN_WAGER:,} a {MAX_WAGER:,} Memilets.".replace(",", ".")
        )


def take_stakes(
    first: tuple[int | str, str],
    second: tuple[int | str, str],
    amount: int,
    database_path: str | Path = DATABASE_PATH,
) -> None:
    """Tira `amount` de cada um (id, nome); se algum não tem saldo, ninguém paga."""
    check_wager(amount)
    for user_id, name in (first, second):
        register_user(user_id, name, database_path)
    with connect(database_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        for user_id, name in (first, second):
            balance = connection.execute(
                "SELECT memilets FROM game_users WHERE discord_user_id = ?", (str(user_id),)
            ).fetchone()["memilets"]
            if balance < amount:
                raise WagerError(
                    f"{name} não tem {amount:,} Memilets para apostar.".replace(",", ".")
                )
        for user_id, _ in (first, second):
            connection.execute(
                "UPDATE game_users SET memilets = memilets - ? WHERE discord_user_id = ?",
                (amount, str(user_id)),
            )


def pay(user_id: int | str, amount: int, database_path: str | Path = DATABASE_PATH) -> None:
    """Paga o pote (ou devolve uma aposta) para `user_id`."""
    with connect(database_path) as connection:
        connection.execute(
            "UPDATE game_users SET memilets = memilets + ? WHERE discord_user_id = ?",
            (amount, str(user_id)),
        )
