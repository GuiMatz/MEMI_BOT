"""Consulta o pool elegível para roleta sem incluir atividade desconhecida."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from src.db import DATABASE_PATH, connect, initialize_database


def get_roleta_pool(
    database_path: str | Path = DATABASE_PATH,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Retorna apenas jogadores ativos confirmados com overall conhecido."""
    if limit is not None and limit < 1:
        raise ValueError("--limit deve ser um inteiro maior que zero.")
    initialize_database(database_path)
    query = """
        SELECT p.id, p.name, p.club_id, p.game_position, p.overall,
               p.overall_source, p.overall_edition
        FROM players AS p
        WHERE p.activity_status = 'active'
          AND p.phase2_candidate = 0
          AND p.overall IS NOT NULL
        ORDER BY p.overall DESC, p.name
    """
    if limit is not None:
        query += " LIMIT ?"
    with connect(database_path) as connection:
        rows = connection.execute(
            query, (limit,) if limit is not None else ()
        ).fetchall()
        return [dict(row) for row in rows]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DATABASE_PATH)
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    try:
        players = get_roleta_pool(args.database, args.limit)
    except ValueError as error:
        parser.error(str(error))
    print(f"Jogadores elegíveis (ativos confirmados e com overall): {len(players):,}")


if __name__ == "__main__":
    main()
