"""Missões diárias e semanais do FUT.AE.

Cada pessoa recebe 3 missões por dia e 2 por semana, sorteadas de forma fixa a partir
do ID e do período (o sorteio não precisa ser guardado). O progresso é registrado pelos
próprios comandos (`record`), e `mm!missoes` mostra o quadro e resgata o que foi
cumprido. Dias e semanas viram no horário de Brasília (semana ISO, de segunda a domingo).
"""

from __future__ import annotations

import random
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.clock import BRASILIA
from src.db import DATABASE_PATH, connect


@dataclass(frozen=True)
class Mission:
    text: str
    event: str
    goal: int
    reward: int


DAILY = {
    "rolar_20": Mission("Role 20 vezes", "roll", 20, 450),
    "contratar_1": Mission("Contrate 1 jogador", "claim", 1, 450),
    "amistoso_1": Mission("Jogue 1 amistoso", "friendly", 1, 360),
    "vitoria_1": Mission("Vença 1 amistoso", "friendly_win", 1, 600),
    "crate_1": Mission("Abra 1 crate", "crate", 1, 450),
    "daily_1": Mission("Resgate o daily", "daily", 1, 240),
}
WEEKLY = {
    "rolar_150": Mission("Role 150 vezes", "roll", 150, 2_700),
    "contratar_8": Mission("Contrate 8 jogadores", "claim", 8, 2_700),
    "craque": Mission("Contrate um jogador 85+", "claim_85", 1, 3_000),
    "vitorias_5": Mission("Vença 5 amistosos", "friendly_win", 5, 3_000),
    "campeonato_3": Mission("Jogue 3 jogos de campeonato", "championship_match", 3, 2_700),
    "crates_5": Mission("Abra 5 crates", "crate", 5, 2_400),
    "torre_1": Mission("Construa 1 andar da torre", "tower", 1, 2_100),
}
DAILY_COUNT = 3
WEEKLY_COUNT = 2


def _now(now: datetime | None) -> datetime:
    value = now or datetime.now(timezone.utc)
    if value.tzinfo is None:
        raise ValueError("now deve incluir um fuso horário.")
    return value.astimezone(BRASILIA)


def periods(now: datetime | None = None) -> tuple[str, str]:
    """`("2026-10-22", "2026-W43")` no horário de Brasília."""
    local = _now(now)
    year, week, _ = local.isocalendar()
    return local.strftime("%Y-%m-%d"), f"{year}-W{week:02d}"


def assigned(
    discord_user_id: int | str, now: datetime | None = None
) -> list[tuple[str, str, str, Mission]]:
    """Missões do período: `(escopo, período, chave, missão)`."""
    day, week = periods(now)
    result = []
    for scope, period, pool, count in (
        ("diaria", day, DAILY, DAILY_COUNT),
        ("semanal", week, WEEKLY, WEEKLY_COUNT),
    ):
        keys = random.Random(f"{discord_user_id}:{period}").sample(sorted(pool), count)
        result.extend((scope, period, key, pool[key]) for key in keys)
    return result


def record(
    connection: sqlite3.Connection,
    discord_user_id: int | str,
    event: str,
    amount: int = 1,
    now: datetime | None = None,
) -> None:
    """Soma progresso às missões do período que contam esse evento."""
    for _, period, key, mission in assigned(discord_user_id, now):
        if mission.event != event:
            continue
        connection.execute(
            """
            INSERT INTO mission_progress (discord_user_id, period, mission_key, progress)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(discord_user_id, period, mission_key)
            DO UPDATE SET progress = MIN(?, mission_progress.progress + excluded.progress)
            """,
            (str(discord_user_id), period, key, min(amount, mission.goal), mission.goal),
        )


def record_event(
    discord_user_id: int | str,
    event: str,
    database_path: str | Path = DATABASE_PATH,
    amount: int = 1,
    now: datetime | None = None,
) -> None:
    with connect(database_path) as connection:
        if connection.execute(
            "SELECT 1 FROM game_users WHERE discord_user_id = ?", (str(discord_user_id),)
        ).fetchone():
            record(connection, discord_user_id, event, amount, now)


def board(
    discord_user_id: int | str,
    database_path: str | Path = DATABASE_PATH,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    missions = assigned(discord_user_id, now)
    with connect(database_path) as connection:
        rows = {
            (row["period"], row["mission_key"]): dict(row)
            for row in connection.execute(
                """
                SELECT period, mission_key, progress, claimed FROM mission_progress
                WHERE discord_user_id = ? AND period IN (?, ?)
                """,
                (str(discord_user_id), *periods(now)),
            )
        }
    result = []
    for scope, period, key, mission in missions:
        row = rows.get((period, key), {})
        result.append(
            {
                "scope": scope,
                "key": key,
                "text": mission.text,
                "goal": mission.goal,
                "progress": int(row.get("progress", 0)),
                "reward": mission.reward,
                "claimed": bool(row.get("claimed", 0)),
            }
        )
    return result


def claim_rewards(
    discord_user_id: int | str,
    database_path: str | Path = DATABASE_PATH,
    now: datetime | None = None,
) -> tuple[int, int]:
    """Resgata as missões cumpridas: devolve (Memilets, quantas missões)."""
    user_id = str(discord_user_id)
    total = count = 0
    with connect(database_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        for _, period, key, mission in assigned(user_id, now):
            cursor = connection.execute(
                """
                UPDATE mission_progress SET claimed = 1
                WHERE discord_user_id = ? AND period = ? AND mission_key = ?
                  AND claimed = 0 AND progress >= ?
                """,
                (user_id, period, key, mission.goal),
            )
            if cursor.rowcount:
                total += mission.reward
                count += 1
        if total:
            connection.execute(
                "UPDATE game_users SET memilets = memilets + ? WHERE discord_user_id = ?",
                (total, user_id),
            )
    return total, count
