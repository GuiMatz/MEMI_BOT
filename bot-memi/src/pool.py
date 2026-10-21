"""Consulta o pool elegível para a roleta (regra em `src.ratings.POOL_SQL`)."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from src.db import DATABASE_PATH, connect, initialize_database
from src.ratings import GAME_OVERALL_SQL, POOL_SQL


def get_roleta_pool(
    database_path: str | Path = DATABASE_PATH,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Retorna os jogadores não inativos com posição conhecida."""
    if limit is not None and limit < 1:
        raise ValueError("--limit deve ser um inteiro maior que zero.")
    initialize_database(database_path)
    query = f"""
        SELECT p.id, p.name, p.display_name, p.club_name, p.game_position,
               {GAME_OVERALL_SQL} AS overall, p.overall_source, p.overall_edition
        FROM players AS p
        WHERE {POOL_SQL}
        ORDER BY overall DESC, p.display_name, p.id
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
    print(f"Jogadores elegíveis (não inativos e com posição): {len(players):,}")


if __name__ == "__main__":
    main()
