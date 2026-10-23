"""Encontra jogadores duplicados e remove os de menor overall.

Dois registros são a mesma pessoa quando têm o mesmo nome normalizado e a mesma
data de nascimento; sem data em um deles, também valem clube ou nacionalidade
iguais. Cartas Cartola e lendas da Enciclopédia nunca entram na comparação.

    python -m src.dedupe            # só mostra o que faria
    python -m src.dedupe --apply    # remove de fato
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from src.db import DATABASE_PATH, connect, normalize_player_name
from src.ratings import GAME_OVERALL_SQL


def _same_person(first: dict[str, Any], second: dict[str, Any]) -> bool:
    if first["birth_date"] and second["birth_date"]:
        return first["birth_date"][:10] == second["birth_date"][:10]
    same_club = bool(first["club_name"]) and normalize_player_name(
        first["club_name"]
    ) == normalize_player_name(second["club_name"])
    same_country = bool(first["nationality"]) and normalize_player_name(
        first["nationality"]
    ) == normalize_player_name(second["nationality"])
    return same_club or same_country


def find_duplicates(database_path: str | Path = DATABASE_PATH) -> list[dict[str, Any]]:
    """Grupos de duplicados; o primeiro jogador de cada grupo é o que fica."""
    with connect(database_path) as connection:
        rows = [dict(row) for row in connection.execute(f"""
                SELECT p.id, COALESCE(NULLIF(p.display_name, ''), p.name) AS name,
                       p.name AS raw_name, p.birth_date, p.club_name, p.nationality,
                       p.photo_path, {GAME_OVERALL_SQL} AS overall,
                       o.discord_user_id AS owner_id
                FROM players AS p
                LEFT JOIN player_ownership AS o ON o.player_id = p.id
                WHERE COALESCE(p.overall_source, '') NOT IN ('Cartola', 'Lenda')
                """)]
    by_name: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_name.setdefault(normalize_player_name(row["raw_name"]), []).append(row)

    groups = []
    for candidates in by_name.values():
        if len(candidates) < 2:
            continue
        clusters: list[list[dict[str, Any]]] = []
        for player in candidates:
            cluster = next(
                (
                    existing
                    for existing in clusters
                    if any(_same_person(player, other) for other in existing)
                ),
                None,
            )
            if cluster is None:
                clusters.append([player])
            else:
                cluster.append(player)
        for cluster in clusters:
            if len(cluster) < 2:
                continue
            cluster.sort(
                key=lambda player: (
                    -(player["overall"] or 0),
                    player["photo_path"] is None,
                    player["id"],
                )
            )
            owners = {player["owner_id"] for player in cluster if player["owner_id"]}
            groups.append({"players": cluster, "conflict": len(owners) > 1})
    groups.sort(key=lambda group: -(group["players"][0]["overall"] or 0))
    return groups


def remove_duplicates(database_path: str | Path = DATABASE_PATH) -> dict[str, int]:
    """Remove duplicados sem conflito, levando posse, bolas, wishes e fontes ao que fica."""
    report = {"removed": 0, "transferred": 0, "skipped": 0}
    groups = find_duplicates(database_path)
    with connect(database_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        for group in groups:
            if group["conflict"]:
                report["skipped"] += 1
                continue
            keep, *drop = group["players"]
            keep_id = int(keep["id"])
            owner = next(
                (player["owner_id"] for player in group["players"] if player["owner_id"]), None
            )
            for player in drop:
                drop_id = int(player["id"])
                if owner and player["owner_id"] == owner and not keep["owner_id"]:
                    connection.execute(
                        "UPDATE player_ownership SET player_id = ? WHERE player_id = ?",
                        (keep_id, drop_id),
                    )
                    keep["owner_id"] = owner
                    report["transferred"] += 1
                balls = connection.execute(
                    "SELECT balls FROM player_balls WHERE player_id = ?", (drop_id,)
                ).fetchone()
                if balls and keep["owner_id"] == player["owner_id"]:
                    connection.execute(
                        """
                        INSERT INTO player_balls (player_id, balls) VALUES (?, ?)
                        ON CONFLICT(player_id) DO UPDATE SET
                            balls = MAX(player_balls.balls, excluded.balls)
                        """,
                        (keep_id, int(balls["balls"])),
                    )
                if connection.execute(
                    "SELECT 1 FROM team_lineups WHERE player_id = ?", (keep_id,)
                ).fetchone():
                    connection.execute("DELETE FROM team_lineups WHERE player_id = ?", (drop_id,))
                else:
                    connection.execute(
                        "UPDATE team_lineups SET player_id = ? WHERE player_id = ?",
                        (keep_id, drop_id),
                    )
                wishes = connection.execute(
                    """
                    INSERT OR IGNORE INTO user_wishes (discord_user_id, player_id, wish_type, created_at)
                    SELECT discord_user_id, ?, wish_type, created_at
                    FROM user_wishes WHERE player_id = ?
                    """,
                    (keep_id, drop_id),
                )
                report["transferred"] += max(0, wishes.rowcount)
                for column in ("proposer_player_id", "recipient_player_id"):
                    connection.execute(
                        f"UPDATE trade_offers SET {column} = ? WHERE {column} = ?",
                        (keep_id, drop_id),
                    )
                connection.execute(
                    "UPDATE OR IGNORE trade_items SET player_id = ? WHERE player_id = ?",
                    (keep_id, drop_id),
                )
                connection.execute("DELETE FROM trade_items WHERE player_id = ?", (drop_id,))
                connection.execute(
                    "UPDATE player_sources SET player_id = ? WHERE player_id = ?",
                    (keep_id, drop_id),
                )
                connection.execute("DELETE FROM players WHERE id = ?", (drop_id,))
                report["removed"] += 1
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--database", type=Path, default=DATABASE_PATH)
    parser.add_argument("--apply", action="store_true", help="remove de fato os duplicados")
    args = parser.parse_args()
    groups = find_duplicates(args.database)
    for group in groups:
        keep, *drop = group["players"]
        marker = " [conflito de dono: mantido]" if group["conflict"] else ""
        print(
            f"{keep['name']}: mantém {keep['id']} ({keep['overall']}); remove "
            + ", ".join(f"{player['id']} ({player['overall']})" for player in drop)
            + marker
        )
    print(f"{len(groups)} grupos encontrados.")
    if args.apply:
        print(remove_duplicates(args.database))
    elif groups:
        print("Nada foi alterado. Rode com --apply para remover.")


if __name__ == "__main__":
    main()
