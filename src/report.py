"""Relata cobertura das fontes, posições, overalls e fotos."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from src.db import DATABASE_PATH, connect, initialize_database

OVERALL_BANDS = (
    ("<50", 1, 49),
    ("50-59", 50, 59),
    ("60-69", 60, 69),
    ("70-79", 70, 79),
    ("80-87", 80, 87),
    ("88-99", 88, 99),
)


def build_report(database_path: str | Path = DATABASE_PATH) -> dict[str, Any]:
    initialize_database(database_path)
    with connect(database_path) as connection:
        total = connection.execute("SELECT COUNT(*) FROM players").fetchone()[0]
        source_counts = {row["source"]: row["total"] for row in connection.execute("""
                SELECT source, COUNT(DISTINCT player_id) AS total
                FROM player_sources GROUP BY source ORDER BY source
                """)}
        positions = {
            row["position"] or "(não mapeada)": row["total"]
            for row in connection.execute("""
                SELECT game_position AS position, COUNT(*) AS total
                FROM players GROUP BY game_position ORDER BY game_position
                """)
        }
        overall_bands = {
            label: connection.execute(
                "SELECT COUNT(*) FROM players WHERE overall BETWEEN ? AND ?",
                (lower, upper),
            ).fetchone()[0]
            for label, lower, upper in OVERALL_BANDS
        }
        overall_bands["sem overall"] = connection.execute(
            "SELECT COUNT(*) FROM players WHERE overall IS NULL"
        ).fetchone()[0]
        missing_photo = connection.execute(
            "SELECT COUNT(*) FROM players WHERE photo_path IS NULL"
        ).fetchone()[0]
    return {
        "players_total": total,
        "source_counts": source_counts,
        "positions": positions,
        "overall_bands": overall_bands,
        "players_without_photo": missing_photo,
    }


def print_report(report: dict[str, Any]) -> None:
    print(f"Total de jogadores: {report['players_total']:,}")
    print("Jogadores por fonte:")
    if report["source_counts"]:
        for source, count in report["source_counts"].items():
            print(f"  {source}: {count:,}")
    else:
        print("  (nenhuma fonte importada)")
    print("Jogadores por posição:")
    for position, count in report["positions"].items():
        print(f"  {position}: {count:,}")
    print("Jogadores por faixa de overall:")
    for band, count in report["overall_bands"].items():
        print(f"  {band}: {count:,}")
    print(f"Sem foto: {report['players_without_photo']:,}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DATABASE_PATH)
    args = parser.parse_args()
    try:
        print_report(build_report(args.database))
    except (OSError, ValueError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
