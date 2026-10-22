"""Centro de Treinamento: a torre de 12 andares do `mm!construir`.

Os andares (1 a 12) podem ser construídos em qualquer ordem. O primeiro custa 1000
Memilets e cada andar construído soma +500 ao preço do próximo. Depois de completar os
12, dá para começar outra construção, que repete os efeitos; uma construção nova só
começa quando a anterior está completa.

`mm!destruir` devolve tudo que foi gasto e apaga os andares, inclusive as bolas dadas
pelo andar 12. Nada disso mexe na simulação das partidas.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.db import DATABASE_PATH, connect
from src.game import register_user
from src.perks import TOWER_FIRST_PRICE, TOWER_FLOORS, TOWER_PRICE_STEP
from src.rolls import MAX_DUPLICATE_BALLS


class TowerError(Exception):
    """Regra da torre que pode ser mostrada ao jogador."""


@dataclass(frozen=True)
class TowerStatus:
    floors: dict[int, int]
    spent: int

    @property
    def total_built(self) -> int:
        return sum(self.floors.values())

    @property
    def construction(self) -> int:
        """Número da construção em andamento (1 = a primeira torre)."""
        return min(self.floors.get(floor, 0) for floor in TOWER_FLOORS) + 1

    def built_in_current(self, floor: int) -> bool:
        return self.floors.get(floor, 0) >= self.construction

    @property
    def next_price(self) -> int:
        return floor_price(self.total_built)


def floor_price(total_built: int) -> int:
    return TOWER_FIRST_PRICE + TOWER_PRICE_STEP * total_built


def tower_status(
    discord_user_id: int | str, database_path: str | Path = DATABASE_PATH
) -> TowerStatus:
    with connect(database_path) as connection:
        return _status(connection, str(discord_user_id))


def _status(connection, user_id: str) -> TowerStatus:
    floors = {
        int(row["floor"]): int(row["times"])
        for row in connection.execute(
            "SELECT floor, times FROM tower_floors WHERE discord_user_id = ?", (user_id,)
        )
    }
    spent = connection.execute(
        "SELECT tower_spent FROM game_users WHERE discord_user_id = ?", (user_id,)
    ).fetchone()
    return TowerStatus(floors, int(spent["tower_spent"]) if spent else 0)


@dataclass(frozen=True)
class BuildResult:
    floor: int
    price: int
    construction: int
    ball_player: dict[str, Any] | None = None


def build_floor(
    discord_user_id: int | str,
    display_name: str,
    floor: int,
    database_path: str | Path = DATABASE_PATH,
    *,
    rng: random.Random | random.SystemRandom | None = None,
) -> BuildResult:
    if floor not in TOWER_FLOORS:
        raise TowerError("Escolha um andar de 1 a 12: `mm!construir 5`.")
    user_id = str(discord_user_id)
    generator = rng or random.SystemRandom()
    register_user(user_id, display_name, database_path)
    with connect(database_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        status = _status(connection, user_id)
        if status.built_in_current(floor):
            raise TowerError(
                f"O andar {floor} já está pronto nesta construção. Termine os outros andares "
                "antes de começar uma construção nova."
            )
        price = status.next_price
        balance = connection.execute(
            "SELECT memilets FROM game_users WHERE discord_user_id = ?", (user_id,)
        ).fetchone()["memilets"]
        if balance < price:
            raise TowerError(f"O andar custa {price:,} Memilets.".replace(",", "."))
        ball_player = None
        if floor == 12:
            ball_player = _pick_ball_player(connection, user_id, generator)
            if ball_player is None:
                raise TowerError(
                    "O andar 12 dá uma bola a um dos 11 escalados, mas nenhum titular pode "
                    "receber bola agora. Escale seu time com `mm!escalar`."
                )
            connection.execute(
                """
                INSERT INTO player_balls (player_id, balls) VALUES (?, 1)
                ON CONFLICT(player_id) DO UPDATE SET
                    balls = player_balls.balls + 1, updated_at = CURRENT_TIMESTAMP
                """,
                (ball_player["id"],),
            )
            connection.execute(
                "INSERT INTO tower_ball_grants (discord_user_id, player_id) VALUES (?, ?)",
                (user_id, ball_player["id"]),
            )
        connection.execute(
            """
            UPDATE game_users
            SET memilets = memilets - ?, tower_spent = tower_spent + ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE discord_user_id = ?
            """,
            (price, price, user_id),
        )
        connection.execute(
            """
            INSERT INTO tower_floors (discord_user_id, floor, times) VALUES (?, ?, 1)
            ON CONFLICT(discord_user_id, floor) DO UPDATE SET times = tower_floors.times + 1
            """,
            (user_id, floor),
        )
        return BuildResult(floor, price, status.construction, ball_player)


def _pick_ball_player(connection, user_id: str, rng) -> dict[str, Any] | None:
    players = [
        dict(row)
        for row in connection.execute(
            """
            SELECT p.id, COALESCE(NULLIF(p.display_name, ''), p.name) AS name,
                   COALESCE(b.balls, 0) AS balls
            FROM team_lineups AS l
            JOIN player_ownership AS o
              ON o.player_id = l.player_id AND o.discord_user_id = l.discord_user_id
            JOIN players AS p ON p.id = l.player_id
            LEFT JOIN player_balls AS b ON b.player_id = p.id
            WHERE l.discord_user_id = ? AND COALESCE(b.balls, 0) < ?
            ORDER BY l.slot_order
            """,
            (user_id, MAX_DUPLICATE_BALLS),
        )
    ]
    return rng.choice(players) if players else None


@dataclass(frozen=True)
class DestroyPreview:
    refund: int
    floors: dict[int, int]
    ball_players: list[str]


def destroy_preview(
    discord_user_id: int | str, database_path: str | Path = DATABASE_PATH
) -> DestroyPreview:
    user_id = str(discord_user_id)
    with connect(database_path) as connection:
        status = _status(connection, user_id)
        names = [
            row["name"]
            for row in connection.execute(
                """
                SELECT COALESCE(NULLIF(p.display_name, ''), p.name) AS name
                FROM tower_ball_grants AS g JOIN players AS p ON p.id = g.player_id
                WHERE g.discord_user_id = ? ORDER BY g.id
                """,
                (user_id,),
            )
        ]
    return DestroyPreview(
        status.spent, {floor: times for floor, times in status.floors.items() if times}, names
    )


def destroy_tower(
    discord_user_id: int | str, database_path: str | Path = DATABASE_PATH
) -> DestroyPreview:
    """Apaga a torre e devolve todos os Memilets gastos nela."""
    user_id = str(discord_user_id)
    preview = destroy_preview(user_id, database_path)
    if not preview.floors:
        raise TowerError("Você ainda não construiu nenhum andar.")
    with connect(database_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        grants = connection.execute(
            "SELECT player_id FROM tower_ball_grants WHERE discord_user_id = ?", (user_id,)
        ).fetchall()
        for grant in grants:
            # A bola do andar 12 sai; quem ficaria sem nenhuma perde a linha de bolas.
            connection.execute(
                "DELETE FROM player_balls WHERE player_id = ? AND balls <= 1",
                (grant["player_id"],),
            )
            connection.execute(
                """
                UPDATE player_balls SET balls = balls - 1, updated_at = CURRENT_TIMESTAMP
                WHERE player_id = ?
                """,
                (grant["player_id"],),
            )
        connection.execute("DELETE FROM tower_ball_grants WHERE discord_user_id = ?", (user_id,))
        connection.execute("DELETE FROM tower_floors WHERE discord_user_id = ?", (user_id,))
        spent = connection.execute(
            "SELECT tower_spent FROM game_users WHERE discord_user_id = ?", (user_id,)
        ).fetchone()["tower_spent"]
        connection.execute(
            """
            UPDATE game_users
            SET memilets = memilets + ?, tower_spent = 0, updated_at = CURRENT_TIMESTAMP
            WHERE discord_user_id = ?
            """,
            (spent, user_id),
        )
    return DestroyPreview(int(spent), preview.floors, preview.ball_players)
