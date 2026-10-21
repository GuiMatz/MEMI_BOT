"""Gera uma tabela dos jogadores que ainda não entram na roleta."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.db import DATABASE_PATH, connect, initialize_database


def default_desktop() -> Path:
    candidates = (
        Path.home() / "OneDrive" / "Área de Trabalho",
        Path.home() / "OneDrive" / "Desktop",
        Path.home() / "Desktop",
    )
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    return Path.home() / "Desktop"


def exclusion_reason(row: dict[str, object]) -> str:
    reasons: list[str] = []
    if row["activity_status"] != "active":
        reasons.append(f"atividade: {row['activity_status']}")
    if row["phase2_candidate"] != 0:
        reasons.append("candidato da fase 2")
    if row["overall"] is None:
        reasons.append("sem overall")
    if row["game_position"] is None:
        reasons.append("sem posição")
    return "; ".join(reasons) or "fora do filtro da roleta"


def generate_table(database_path: Path, output_path: Path) -> int:
    initialize_database(database_path)
    with connect(database_path) as connection:
        rows = [
            dict(row)
            for row in connection.execute(
                """
                SELECT id, name, display_name, club_name, game_position, overall,
                       activity_status, phase2_candidate, needs_position_review,
                       photo_path
                FROM players
                WHERE activity_status != 'active'
                   OR phase2_candidate != 0
                   OR overall IS NULL
                   OR game_position IS NULL
                ORDER BY
                    CASE WHEN overall IS NULL THEN 1 ELSE 0 END,
                    overall DESC,
                    display_name COLLATE NOCASE,
                    id
                """
            )
        ]

    output_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = (
        "id",
        "nome",
        "clube",
        "posicao",
        "overall",
        "atividade",
        "fase_2",
        "revisao_posicao",
        "tem_foto",
        "motivo_fora_da_roleta",
    )
    with output_path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames, delimiter=";")
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "id": row["id"],
                    "nome": row["display_name"] or row["name"],
                    "clube": row["club_name"] or "Passe livre",
                    "posicao": row["game_position"] or "",
                    "overall": row["overall"] or "",
                    "atividade": row["activity_status"],
                    "fase_2": "sim" if row["phase2_candidate"] else "não",
                    "revisao_posicao": "sim" if row["needs_position_review"] else "não",
                    "tem_foto": "sim" if row["photo_path"] else "não",
                    "motivo_fora_da_roleta": exclusion_reason(row),
                }
            )
    return len(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DATABASE_PATH)
    parser.add_argument(
        "--output",
        type=Path,
        default=default_desktop() / "jogadores_fora_da_roleta.csv",
    )
    args = parser.parse_args()
    total = generate_table(args.database, args.output)
    print(f"Tabela criada: {args.output}")
    print(f"Jogadores fora da roleta: {total}")


if __name__ == "__main__":
    main()
