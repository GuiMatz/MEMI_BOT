"""Consultas e operações básicas da conta de jogo."""

from __future__ import annotations

import sqlite3
from typing import Any

from src.db import DATABASE_PATH, GAME_POSITIONS, ROOT, connect, normalize_player_name

CATALOG_ASSET_DIRECTORIES = {
    "uniformes": (
        "teams/camisas",
        "teams/camisas2",
        "teams/camisas3",
    ),
    "logos": ("teams/escudos",),
}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif"}


def get_collection_left(database_path=DATABASE_PATH) -> dict[str, int]:
    """Retorna jogadores não reivindicados e assets disponíveis no catálogo local."""
    with connect(database_path) as connection:
        row = connection.execute(
            """
            SELECT COUNT(*) AS total,
                   COALESCE(SUM(
                       NOT EXISTS (
                           SELECT 1 FROM player_ownership AS o
                           WHERE o.player_id = p.id
                       )
                   ), 0) AS remaining
            FROM players AS p
            WHERE p.activity_status = 'active'
              AND p.phase2_candidate = 0
              AND p.overall IS NOT NULL
              AND p.game_position IS NOT NULL
            """
        ).fetchone()

    counts = {
        "jogadores": int(row["remaining"]),
        "jogadores_total": int(row["total"]),
    }
    asset_root = ROOT / "data" / "assets" / "brasfoot"
    for label, directories in CATALOG_ASSET_DIRECTORIES.items():
        counts[label] = sum(
            1
            for directory in directories
            for path in (asset_root / directory).rglob("*")
            if path.is_file() and path.suffix.casefold() in IMAGE_SUFFIXES
        )
    return counts


def register_user(
    discord_user_id: int | str,
    display_name: str,
    database_path=DATABASE_PATH,
) -> dict[str, Any]:
    """Cria a conta na primeira utilização e mantém o nome atual do Discord."""
    user_id = str(discord_user_id)
    with connect(database_path) as connection:
        connection.execute(
            """
            INSERT INTO game_users (discord_user_id, display_name)
            VALUES (?, ?)
            ON CONFLICT(discord_user_id) DO UPDATE SET
                display_name = excluded.display_name,
                updated_at = CURRENT_TIMESTAMP
            """,
            (user_id, display_name),
        )
        row = connection.execute(
            """
            SELECT discord_user_id, display_name, memilets, created_at
            FROM game_users
            WHERE discord_user_id = ?
            """,
            (user_id,),
        ).fetchone()
        return dict(row)


def get_wallet(
    discord_user_id: int | str,
    database_path=DATABASE_PATH,
) -> dict[str, Any]:
    """Retorna o saldo persistido de uma conta de jogo."""
    with connect(database_path) as connection:
        row = connection.execute(
            """
            SELECT discord_user_id, display_name, memilets
            FROM game_users
            WHERE discord_user_id = ?
            """,
            (str(discord_user_id),),
        ).fetchone()
    if row is None:
        raise LookupError("A conta de jogo ainda não foi cadastrada.")
    return dict(row)


def list_owned_players(
    discord_user_id: int | str,
    database_path=DATABASE_PATH,
    limit: int = 20,
    *,
    position: str | None = None,
    min_overall: int | None = None,
    max_overall: int | None = None,
    club_query: str | None = None,
    league_query: str | None = None,
    sort_by: str = "acquired",
) -> list[dict[str, Any]]:
    """Lista os jogadores do elenco de uma pessoa por ordem de aquisição."""
    if limit < 1:
        raise ValueError("limit deve ser maior que zero.")
    if position is not None and position not in GAME_POSITIONS:
        raise ValueError("position inválida.")
    if min_overall is not None and not 1 <= min_overall <= 100:
        raise ValueError("min_overall deve ficar entre 1 e 100.")
    if max_overall is not None and not 1 <= max_overall <= 100:
        raise ValueError("max_overall deve ficar entre 1 e 100.")
    if min_overall is not None and max_overall is not None and min_overall > max_overall:
        raise ValueError("min_overall não pode ser maior que max_overall.")
    order_by = {
        "acquired": "o.acquired_at, o.player_id",
        "overall": "overall DESC, name, p.id",
        "name": "name COLLATE NOCASE, p.id",
    }.get(sort_by)
    if order_by is None:
        raise ValueError("sort_by inválido.")
    clauses = ["o.discord_user_id = ?"]
    parameters: list[Any] = [str(discord_user_id)]
    if position is not None:
        clauses.append("p.game_position = ?")
        parameters.append(position)
    effective_overall = "MIN(100, p.overall + COALESCE(b.balls, 0))"
    if min_overall is not None:
        clauses.append(f"{effective_overall} >= ?")
        parameters.append(min_overall)
    if max_overall is not None:
        clauses.append(f"{effective_overall} <= ?")
        parameters.append(max_overall)
    if club_query:
        clauses.append("normalize_player_name(p.club_name) LIKE ?")
        parameters.append(f"%{normalize_player_name(club_query)}%")
    if league_query:
        clauses.append(
            """
            EXISTS (
                SELECT 1 FROM player_sources AS ps
                WHERE ps.player_id = p.id
                  AND normalize_player_name(ps.source_league_name) LIKE ?
            )
            """
        )
        parameters.append(f"%{normalize_player_name(league_query)}%")
    parameters.append(limit)
    with connect(database_path) as connection:
        rows = connection.execute(
            f"""
            SELECT p.id, COALESCE(NULLIF(p.display_name, ''), p.name) AS name,
                   p.club_name, p.game_position,
                   {effective_overall} AS overall,
                   COALESCE(b.balls, 0) AS balls, o.acquired_at
            FROM player_ownership AS o
            JOIN players AS p ON p.id = o.player_id
            LEFT JOIN player_balls AS b ON b.player_id = p.id
            WHERE {' AND '.join(clauses)}
            ORDER BY {order_by}
            LIMIT ?
            """,
            parameters,
        ).fetchall()
    return [dict(row) for row in rows]


def search_players(
    query: str,
    database_path=DATABASE_PATH,
    limit: int = 5,
) -> list[dict[str, Any]]:
    """Busca jogadores pelo nome, sem exigir acentos ou a pontuação exata."""
    normalized_query = normalize_player_name(query)
    if not normalized_query:
        return []
    if limit < 1:
        raise ValueError("limit deve ser maior que zero.")
    with connect(database_path) as connection:
        if query.strip().isdigit():
            rows = connection.execute(
                """
                SELECT id, name, display_name, club_name, nationality,
                       game_position, overall, overall_source, overall_edition
                FROM players
                WHERE id = ?
                """,
                (int(query.strip()),),
            ).fetchall()
            return [dict(row) for row in rows]
        rows = connection.execute(
            """
            SELECT id, name, display_name, club_name, nationality,
                   game_position, overall, overall_source, overall_edition
            FROM players
            WHERE normalize_player_name(name) LIKE ?
               OR normalize_player_name(display_name) LIKE ?
            ORDER BY
                CASE WHEN normalize_player_name(name) = ? THEN 0 ELSE 1 END,
                overall DESC, name, id
            LIMIT ?
            """,
            (
                f"%{normalized_query}%",
                f"%{normalized_query}%",
                normalized_query,
                limit,
            ),
        ).fetchall()
    return [dict(row) for row in rows]


def list_players_by_team_or_series(
    query: str,
    database_path=DATABASE_PATH,
    *,
    limit: int = 5000,
) -> list[dict[str, Any]]:
    """Lista jogadores ativos de um clube ou liga, ordenados por overall."""
    normalized_query = normalize_player_name(query)
    if not normalized_query:
        return []
    if limit < 1:
        raise ValueError("limit deve ser maior que zero.")
    pattern = f"%{normalized_query}%"
    with connect(database_path) as connection:
        rows = connection.execute(
            """
            SELECT DISTINCT p.id,
                   COALESCE(NULLIF(p.display_name, ''), p.name) AS name,
                   p.club_name, p.game_position, p.overall,
                   COALESCE(ps.source_league_name, '') AS league_name
            FROM players AS p
            LEFT JOIN player_sources AS ps ON ps.player_id = p.id
            WHERE p.activity_status = 'active'
              AND p.phase2_candidate = 0
              AND p.overall IS NOT NULL
              AND (
                  normalize_player_name(p.club_name) LIKE ?
                  OR normalize_player_name(ps.source_league_name) LIKE ?
              )
            ORDER BY p.overall DESC, name COLLATE NOCASE, p.id
            LIMIT ?
            """,
            (pattern, pattern, limit),
        ).fetchall()
    return [dict(row) for row in rows]


def get_top_players(
    database_path=DATABASE_PATH,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Retorna os jogadores ativos e elegíveis com maior overall."""
    if limit < 1:
        raise ValueError("limit deve ser maior que zero.")
    with connect(database_path) as connection:
        rows = connection.execute(
            """
            SELECT id, COALESCE(NULLIF(display_name, ''), name) AS name,
                   club_name, game_position,
                   MIN(100, overall + COALESCE(b.balls, 0)) AS overall
            FROM players AS p
            LEFT JOIN player_balls AS b ON b.player_id = p.id
            WHERE activity_status = 'active'
              AND p.phase2_candidate = 0
              AND p.overall IS NOT NULL
              AND p.game_position IS NOT NULL
            ORDER BY overall DESC, name, p.id
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def get_catalog_entries(
    category: str,
    database_path=DATABASE_PATH,
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    """Lista uma categoria do catálogo em ordem estável para os embeds."""
    if limit < 1 or offset < 0:
        raise ValueError("limite e deslocamento inválidos.")
    normalized = normalize_player_name(category)
    if normalized in {"jogador", "jogadores", "player", "players"}:
        with connect(database_path) as connection:
            total = connection.execute(
                """SELECT COUNT(*) AS total FROM players
                   WHERE activity_status = 'active' AND phase2_candidate = 0
                     AND overall IS NOT NULL AND game_position IS NOT NULL"""
            ).fetchone()["total"]
            rows = connection.execute(
                """SELECT id, COALESCE(NULLIF(display_name, ''), name) AS name,
                          overall, game_position
                   FROM players
                   WHERE activity_status = 'active' AND phase2_candidate = 0
                     AND overall IS NOT NULL AND game_position IS NOT NULL
                   ORDER BY overall DESC, name COLLATE NOCASE, id
                   LIMIT ? OFFSET ?""",
                (limit, offset),
            ).fetchall()
        return [dict(row) for row in rows], int(total)

    asset_dirs = {
        "tecnico": ("data/assets/brasfoot",),
        "tecnicos": ("data/assets/brasfoot",),
        "uniforme": ("data/assets/brasfoot/teams/camisas",),
        "uniformes": ("data/assets/brasfoot/teams/camisas",),
        "mascote": ("data/assets/cartola/mascots",),
        "mascotes": ("data/assets/cartola/mascots",),
        "estadio": ("data/assets/brasfoot",),
        "estadios": ("data/assets/brasfoot",),
        "liga": ("data/assets/brasfoot",),
        "ligas": ("data/assets/brasfoot",),
        "emblema": ("data/assets/brasfoot/teams/escudos",),
        "emblemas": ("data/assets/brasfoot/teams/escudos",),
    }
    if normalized == "cartola":
        with connect(database_path) as connection:
            players = connection.execute(
                """SELECT id, COALESCE(NULLIF(display_name, ''), name) AS name,
                          overall, game_position
                   FROM players
                   WHERE overall_source = 'Cartola'
                   ORDER BY overall DESC, name COLLATE NOCASE, id"""
            ).fetchall()
        entries = [dict(row) for row in players]
        directories = ("data/assets/cartola/logos", "data/assets/cartola/mascots")
        paths = sorted(
            path for directory in directories
            for path in (ROOT / directory).rglob("*")
            if path.is_file() and path.suffix.casefold() in IMAGE_SUFFIXES
        )
        entries.extend(
            {"name": path.stem, "path": str(path.relative_to(ROOT))}
            for path in paths
        )
        return entries[offset : offset + limit], len(entries)
    else:
        directories = asset_dirs.get(normalized)
    if directories is None:
        return [], 0
    table_categories = {
        "tecnicos": ("coaches", "name"),
        "tecnico": ("coaches", "name"),
        "estadios": ("stadiums", "name"),
        "estadio": ("stadiums", "name"),
        "ligas": ("leagues", "name"),
        "liga": ("leagues", "name"),
    }
    if normalized in table_categories:
        table, column = table_categories[normalized]
        with connect(database_path) as connection:
            rows = connection.execute(
                f"SELECT {column} AS name FROM {table} ORDER BY name COLLATE NOCASE"
            ).fetchall()
        entries = [{"name": row["name"]} for row in rows]
        return entries[offset : offset + limit], len(entries)
    paths = sorted(
        path for directory in directories
        for path in (ROOT / directory).rglob("*")
        if path.is_file() and path.suffix.casefold() in IMAGE_SUFFIXES
    )
    entries = [{"name": path.stem, "path": str(path.relative_to(ROOT))} for path in paths]
    return entries[offset : offset + limit], len(entries)


def claim_player(
    player_id: int,
    discord_user_id: int | str,
    database_path=DATABASE_PATH,
) -> bool:
    """Entrega um jogador uma única vez; a chave primária evita posse duplicada."""
    try:
        with connect(database_path) as connection:
            connection.execute(
                """
                INSERT INTO player_ownership (player_id, discord_user_id)
                VALUES (?, ?)
                """,
                (player_id, str(discord_user_id)),
            )
    except sqlite3.IntegrityError as error:
        if "player_ownership.player_id" in str(error) or "UNIQUE constraint" in str(
            error
        ):
            return False
        raise
    return True
