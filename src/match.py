"""Gera candidatos de correspondência entre fontes para revisão humana."""

from __future__ import annotations

import argparse
import csv
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from rapidfuzz import fuzz

from src.db import DATABASE_PATH, connect, initialize_database

ROOT = Path(__file__).resolve().parents[1]
REVIEW_PATH = ROOT / "data" / "review" / "player_matches.csv"
DEFAULT_THRESHOLD = 75.0


@dataclass(frozen=True)
class PlayerSource:
    player_id: int
    source: str
    source_id: str
    name: str
    normalized_name: str
    birth_date: str | None
    club: str | None
    normalized_club: str | None


def _normalize(value: str | None) -> str | None:
    if not value:
        return None
    normalized = unicodedata.normalize("NFKD", value)
    normalized = "".join(
        character for character in normalized if not unicodedata.combining(character)
    )
    normalized = re.sub(r"[^a-zA-Z0-9]+", " ", normalized).lower().strip()
    return normalized or None


def _normalize_club(value: str | None) -> str | None:
    normalized = _normalize(value)
    if normalized is None:
        return None
    normalized = re.sub(r"\b(football club|fc|cf|afc|ac|club)\b", " ", normalized)
    return re.sub(r"\s+", " ", normalized).strip() or None


def _normalize_birth_date(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10]).isoformat()
    except ValueError:
        return None


def _player_sources(connection) -> list[PlayerSource]:
    rows = connection.execute("""
        SELECT ps.player_id, ps.source, ps.source_id,
               COALESCE(ps.source_name, p.name) AS name,
               p.birth_date,
               COALESCE(ps.source_club_name, c.name) AS club
        FROM player_sources AS ps
        JOIN players AS p ON p.id = ps.player_id
        LEFT JOIN clubs AS c ON c.id = p.club_id
        ORDER BY ps.source, ps.player_id
        """)
    result = []
    for row in rows:
        normalized_name = _normalize(row["name"])
        if normalized_name is None:
            continue
        result.append(
            PlayerSource(
                player_id=row["player_id"],
                source=row["source"],
                source_id=row["source_id"],
                name=row["name"],
                normalized_name=normalized_name,
                birth_date=_normalize_birth_date(row["birth_date"]),
                club=row["club"],
                normalized_club=_normalize_club(row["club"]),
            )
        )
    return result


def _candidate_pairs(
    players: list[PlayerSource], threshold: float
) -> list[dict[str, object]]:
    by_source: dict[str, list[PlayerSource]] = defaultdict(list)
    for player in players:
        by_source[player.source].append(player)

    candidates: dict[tuple[int, int], dict[str, object]] = {}
    sources = sorted(by_source)
    for source_index, source_a in enumerate(sources):
        for source_b in sources[source_index + 1 :]:
            group_a = by_source[source_a]
            group_b = by_source[source_b]
            b_by_birth: dict[str, list[PlayerSource]] = defaultdict(list)
            for player in group_b:
                if player.birth_date:
                    b_by_birth[player.birth_date].append(player)

            for player_a in group_a:
                if not player_a.birth_date:
                    continue
                for player_b in b_by_birth.get(player_a.birth_date, ()):
                    if player_a.player_id == player_b.player_id:
                        continue
                    birth_match = True
                    club_match = bool(
                        player_a.normalized_club
                        and player_b.normalized_club
                        and player_a.normalized_club == player_b.normalized_club
                    )
                    if not (birth_match or club_match):
                        continue
                    similarity = float(
                        fuzz.WRatio(player_a.normalized_name, player_b.normalized_name)
                    )
                    if similarity < threshold:
                        continue

                    if player_a.player_id < player_b.player_id:
                        first, second = player_a, player_b
                    else:
                        first, second = player_b, player_a
                    key = (first.player_id, second.player_id)
                    candidates[key] = {
                        "player_a_id": first.player_id,
                        "player_b_id": second.player_id,
                        "source_a": first.source,
                        "source_b": second.source,
                        "source_id_a": first.source_id,
                        "source_id_b": second.source_id,
                        "name_a": first.name,
                        "name_b": second.name,
                        "birth_date_a": first.birth_date,
                        "birth_date_b": second.birth_date,
                        "club_a": first.club,
                        "club_b": second.club,
                        "name_similarity": round(similarity, 2),
                        "birth_date_match": int(birth_match),
                        "club_match": int(club_match),
                        "status": "pending",
                    }
    return sorted(
        candidates.values(),
        key=lambda candidate: (
            not bool(candidate["club_match"]),
            -float(candidate["name_similarity"]),
            int(candidate["player_a_id"]),
            int(candidate["player_b_id"]),
        ),
    )


def _write_review_csv(path: Path, candidates: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "player_a_id",
        "player_b_id",
        "source_a",
        "source_id_a",
        "name_a",
        "birth_date_a",
        "club_a",
        "source_b",
        "source_id_b",
        "name_b",
        "birth_date_b",
        "club_b",
        "name_similarity",
        "birth_date_match",
        "club_match",
        "status",
    ]
    with path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        writer.writerows(candidates)


def find_matches(
    database_path: str | Path = DATABASE_PATH,
    output_path: str | Path = REVIEW_PATH,
    threshold: float = DEFAULT_THRESHOLD,
) -> int:
    """Cria candidatos pendentes; nenhum jogador é unido automaticamente."""
    if not 0 <= threshold <= 100:
        raise ValueError("O limite de similaridade deve estar entre 0 e 100.")
    initialize_database(database_path)
    with connect(database_path) as connection:
        players = _player_sources(connection)
        candidates = _candidate_pairs(players, threshold)
        for candidate in candidates:
            connection.execute(
                """
                INSERT INTO player_match_candidates (
                    player_a_id, player_b_id, source_a, source_b,
                    name_similarity, birth_date_match, club_match
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(player_a_id, player_b_id) DO UPDATE SET
                    source_a = excluded.source_a,
                    source_b = excluded.source_b,
                    name_similarity = excluded.name_similarity,
                    birth_date_match = excluded.birth_date_match,
                    club_match = excluded.club_match
                """,
                (
                    candidate["player_a_id"],
                    candidate["player_b_id"],
                    candidate["source_a"],
                    candidate["source_b"],
                    candidate["name_similarity"],
                    candidate["birth_date_match"],
                    candidate["club_match"],
                ),
            )
    _write_review_csv(Path(output_path), candidates)
    return len(candidates)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DATABASE_PATH)
    parser.add_argument("--output", type=Path, default=REVIEW_PATH)
    parser.add_argument(
        "--threshold",
        type=float,
        default=DEFAULT_THRESHOLD,
        help=f"similaridade mínima de nome, 0-100 (padrão: {DEFAULT_THRESHOLD:g})",
    )
    args = parser.parse_args()
    try:
        count = find_matches(args.database, args.output, args.threshold)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    print(
        f"{count:,} candidatos para revisão manual; nenhum merge foi aplicado. "
        f"Arquivo: {args.output}"
    )


if __name__ == "__main__":
    main()
