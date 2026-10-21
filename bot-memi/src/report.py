"""Relata cobertura das fontes, posições, overalls e fotos."""

from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path
from typing import Any

from src.db import (
    DATABASE_PATH,
    connect,
    initialize_database,
    normalize_player_name,
)

OVERALL_BANDS = (
    ("1-59", 1, 59),
    ("60-69", 60, 69),
    ("70-79", 70, 79),
    ("80-86", 80, 86),
    ("87-89", 87, 89),
    ("90-94", 90, 94),
    ("95-97", 95, 97),
    ("98-99", 98, 99),
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
            row["position"] or "(não mapeada)": row["total"] for row in connection.execute("""
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
        ea_match_stats = connection.execute("""
                SELECT
                    COALESCE(SUM(matched), 0) AS matched,
                    COALESCE(SUM(ambiguous), 0) AS ambiguous,
                    COALESCE(SUM(new_players), 0) AS new_players,
                    COALESCE(SUM(brazil_excluded), 0) AS brazil_excluded
                FROM ea_import_stats
                """).fetchone()
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
        position_review = connection.execute(
            "SELECT COUNT(*) FROM players WHERE needs_position_review = 1"
        ).fetchone()[0]
        missing_photo = connection.execute(
            "SELECT COUNT(*) FROM players WHERE photo_path IS NULL"
        ).fetchone()[0]
        inactive_players = connection.execute(
            "SELECT COUNT(*) FROM players WHERE activity_status = 'inactive'"
        ).fetchone()[0]
        unknown_activity = connection.execute(
            "SELECT COUNT(*) FROM players WHERE activity_status = 'unknown'"
        ).fetchone()[0]
        phase2_candidates = connection.execute(
            "SELECT COUNT(*) FROM players WHERE phase2_candidate = 1"
        ).fetchone()[0]
        roleta_eligible = connection.execute("""
            SELECT COUNT(*) FROM players
            WHERE activity_status = 'active'
              AND phase2_candidate = 0
              AND overall IS NOT NULL
              AND game_position IS NOT NULL
            """).fetchone()[0]
        clubs_without_players_list = [dict(row) for row in connection.execute("""
                SELECT c.id, c.name, c.transfermarkt_id
                FROM clubs AS c
                WHERE NOT EXISTS (
                    SELECT 1 FROM players AS p
                    WHERE p.club_name = c.name
                )
                ORDER BY c.name, c.id
                """)]
        top_overall = [dict(row) for row in connection.execute("""
                SELECT p.id, p.name, p.display_name, p.overall, p.overall_source,
                       p.overall_edition, p.club_name
                FROM players AS p
                WHERE p.overall IS NOT NULL
                ORDER BY p.overall DESC, p.display_name COLLATE NOCASE, p.id
                LIMIT 30
                """)]
        top_market_value = [dict(row) for row in connection.execute("""
                SELECT p.id, p.name, p.display_name, p.market_value_eur, p.overall,
                       p.overall_source, p.overall_edition, p.club_name
                FROM players AS p
                WHERE p.market_value_eur IS NOT NULL
                ORDER BY p.market_value_eur DESC, p.display_name COLLATE NOCASE, p.id
                LIMIT 30
                """)]
        duplicate_tm_ids = connection.execute("""
                SELECT COALESCE(SUM(duplicate_count - 1), 0)
                FROM (
                    SELECT COUNT(*) AS duplicate_count
                    FROM players
                    WHERE tm_player_id IS NOT NULL
                    GROUP BY tm_player_id
                    HAVING COUNT(*) > 1
                )
                """).fetchone()[0]
        name_birth_rows = connection.execute("""
                SELECT name, birth_date FROM players
                WHERE birth_date IS NOT NULL
                """).fetchall()
    duplicate_name_births: dict[tuple[str, str], int] = Counter()
    for row in name_birth_rows:
        normalized_name = normalize_player_name(row["name"])
        birth_date = str(row["birth_date"])[:10]
        if normalized_name and birth_date:
            duplicate_name_births[(normalized_name, birth_date)] += 1
    duplicate_name_birth_groups = sum(count > 1 for count in duplicate_name_births.values())
    duplicate_name_birth_players = sum(
        count - 1 for count in duplicate_name_births.values() if count > 1
    )
    return {
        "players_total": total,
        "source_counts": source_counts,
        "cross_source_players": cross_source_players,
        "pending_match_candidates": pending_match_candidates,
        "ea_matched": ea_match_stats["matched"],
        "ea_ambiguous": ea_match_stats["ambiguous"],
        "ea_new_players": ea_match_stats["new_players"],
        "ea_brazil_excluded": ea_match_stats["brazil_excluded"],
        "duplicate_transfermarkt_ids": duplicate_tm_ids,
        "duplicate_name_birth_groups": duplicate_name_birth_groups,
        "duplicate_name_birth_players": duplicate_name_birth_players,
        "positions": positions,
        "players_without_position": players_without_position,
        "position_review": position_review,
        "overall_bands": overall_bands,
        "overall_87_plus": overall_87_plus,
        "players_without_photo": missing_photo,
        "clubs_without_players": len(clubs_without_players_list),
        "clubs_without_players_list": clubs_without_players_list,
        "inactive_players": inactive_players,
        "unknown_activity": unknown_activity,
        "phase2_candidates": phase2_candidates,
        "roleta_eligible": roleta_eligible,
        "top_overall": top_overall,
        "top_market_value": top_market_value,
    }


def print_report(report: dict[str, Any]) -> None:
    print(f"Total de jogadores: {report['players_total']:,}")
    print("Jogadores por fonte:")
    if report["source_counts"]:
        for source, count in report["source_counts"].items():
            print(f"  {source}: {count:,}")
    else:
        print("  (nenhuma fonte importada)")
    print("Jogadores confirmados em mais de uma fonte: " f"{report['cross_source_players']:,}")
    print(
        "Candidatos de cruzamento pendentes de revisão: " f"{report['pending_match_candidates']:,}"
    )
    print(
        "Importação EA: "
        f"{report['ea_matched']:,} casados, "
        f"{report['ea_ambiguous']:,} ambíguos, "
        f"{report['ea_new_players']:,} novos, "
        f"{report['ea_brazil_excluded']:,} novos do Brasil ignorados"
    )
    print("IDs Transfermarkt duplicados: " f"{report['duplicate_transfermarkt_ids']:,}")
    print(
        "Duplicados por nome e data de nascimento: "
        f"{report['duplicate_name_birth_players']:,} registros excedentes "
        f"em {report['duplicate_name_birth_groups']:,} grupos"
    )
    print("Jogadores por posição:")
    for position, count in report["positions"].items():
        print(f"  {position}: {count:,}")
    print(f"Sem posição de jogo: {report['players_without_position']:,}")
    print("Jogadores marcados para revisão de posição: " f"{report['position_review']:,}")
    print("Jogadores por faixa de overall:")
    for band, count in report["overall_bands"].items():
        print(f"  {band}: {count:,}")
    print(f"Overall 87 ou maior: {report['overall_87_plus']:,}")
    print(
        f"Atividade: {report['inactive_players']:,} inativos, "
        f"{report['unknown_activity']:,} desconhecidos, "
        f"{report['phase2_candidates']:,} candidatos à fase 2"
    )
    print(f"Elegíveis para roleta: {report['roleta_eligible']:,}")
    print(f"Sem foto: {report['players_without_photo']:,}")
    print(f"Clubes sem jogador: {report['clubs_without_players']:,}")
    print("Top 30 por overall:")
    for player in report["top_overall"]:
        club = player["club_name"] or "sem clube"
        edition = f" [{player['overall_edition']}]" if player["overall_edition"] else ""
        print(
            f"  {player['overall']:>2} | {player['display_name']} | {club}"
            f"{edition} (ID {player['id']})"
        )
    print("Top 30 por valor de mercado:")
    for player in report["top_market_value"]:
        club = player["club_name"] or "sem clube"
        value = player["market_value_eur"]
        print(
            f"€{value:,} | {player['display_name']} | {club} "
            f"(overall {player['overall'] if player['overall'] is not None else '—'}, "
            f"ID {player['id']})"
        )
    print("Clubes sem jogador associado:")
    for club in report["clubs_without_players_list"]:
        print(
            f"  {club['name']} (ID {club['id']}, "
            f"Transfermarkt {club['transfermarkt_id'] or 'sem ID'})"
        )


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
