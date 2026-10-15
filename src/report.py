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
        cross_source_players = connection.execute("""
                SELECT COUNT(*) FROM (
                    SELECT player_id
                    FROM player_sources
                    GROUP BY player_id
                    HAVING COUNT(DISTINCT source) > 1
                )
                """).fetchone()[0]
        pending_match_candidates = connection.execute("""
                SELECT COUNT(*) FROM player_match_candidates
                WHERE status = 'pending'
                """).fetchone()[0]
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
        overall_87_plus = connection.execute(
            "SELECT COUNT(*) FROM players WHERE overall >= 87"
        ).fetchone()[0]
        players_without_position = connection.execute(
            "SELECT COUNT(*) FROM players WHERE game_position IS NULL"
        ).fetchone()[0]
        missing_photo = connection.execute(
            "SELECT COUNT(*) FROM players WHERE photo_path IS NULL"
        ).fetchone()[0]
        clubs_without_players = connection.execute("""
            SELECT COUNT(*) FROM clubs AS c
            WHERE NOT EXISTS (
                SELECT 1 FROM players AS p WHERE p.club_id = c.id
            )
            """).fetchone()[0]
    return {
        "players_total": total,
        "source_counts": source_counts,
        "cross_source_players": cross_source_players,
        "pending_match_candidates": pending_match_candidates,
        "positions": positions,
        "players_without_position": players_without_position,
        "overall_bands": overall_bands,
        "overall_87_plus": overall_87_plus,
        "players_without_photo": missing_photo,
        "clubs_without_players": clubs_without_players,
    }


def print_report(report: dict[str, Any]) -> None:
    print(f"Total de jogadores: {report['players_total']:,}")
    print("Jogadores por fonte:")
    if report["source_counts"]:
        for source, count in report["source_counts"].items():
            print(f"  {source}: {count:,}")
    else:
        print("  (nenhuma fonte importada)")
    print(
        "Jogadores confirmados em mais de uma fonte: "
        f"{report['cross_source_players']:,}"
    )
    print(
        "Candidatos de cruzamento pendentes de revisão: "
        f"{report['pending_match_candidates']:,}"
    )
    print("Jogadores por posição:")
    for position, count in report["positions"].items():
        print(f"  {position}: {count:,}")
    print(f"Sem posição de jogo: {report['players_without_position']:,}")
    print("Jogadores por faixa de overall:")
    for band, count in report["overall_bands"].items():
        print(f"  {band}: {count:,}")
    print(f"Overall 87 ou maior: {report['overall_87_plus']:,}")
    print(f"Sem foto: {report['players_without_photo']:,}")
    print(f"Clubes sem jogador: {report['clubs_without_players']:,}")


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
