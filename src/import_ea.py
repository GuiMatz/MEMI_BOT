"""Importa datasets EA FC para o SQLite sem unir jogadores entre fontes."""

from __future__ import annotations

import argparse
import csv
import re
from collections.abc import Iterator
from datetime import date
from pathlib import Path
import sqlite3
import tempfile
from typing import Any

import pandas as pd

from src.db import (
    DATABASE_PATH,
    connect,
    initialize_database,
    normalize_player_name,
    refresh_display_names,
)

ROOT = Path(__file__).resolve().parents[1]
RAW_DIRECTORY = ROOT / "data" / "raw"

POSITION_MAP = {
    "ST": "CA",
    "CF": "CA",
    "LW": "PE",
    "LM": "PE",
    "RW": "PD",
    "RM": "PD",
    "CAM": "MEI",
    "CM": "MC",
    "CDM": "MC",
    "LB": "LE",
    "LWB": "LE",
    "RB": "LD",
    "RWB": "LD",
    "CB": "ZAG",
    "GK": "GOL",
}

COLUMN_ALIASES = {
    "player_id": (
        "stats_id",
        "player_id",
        "sofifa_id",
        "ea_id",
        "base_id",
        "id",
    ),
    "name": (
        "full_name",
        "long_name",
        "common_name",
        "player_name",
        "name",
        "short_name",
    ),
    "position": ("position", "player_position", "player_positions", "positions"),
    "alternate_position": (
        "alternate_positions",
        "alt_positions",
        "position_alt",
        "position_alt1",
    ),
    "alternate_position_2": ("position_alt_2", "position_alt2"),
    "alternate_position_3": ("position_alt_3", "position_alt3"),
    "overall": ("overall", "overall_rating", "rating", "ovr"),
    "birth_date": ("dob", "date_of_birth", "birth_date", "birthdate"),
    "nationality": (
        "nationality_name",
        "nationality",
        "nation_name",
        "nation",
        "country",
    ),
    "club": ("club_name", "team_name", "club", "team"),
    "league": ("league_name", "league", "competition"),
    "league_country": (
        "league_country",
        "league_country_name",
        "competition_country",
        "country",
    ),
    "age": ("age", "player_age"),
    "photo": ("player_face_url", "photo_url", "image_url", "photo"),
    "edition": ("edition", "fifa_version", "game_version"),
    "source_date": ("snapshot_date", "fifa_update_date", "update_date"),
    "first_name": ("first_name", "firstname"),
    "last_name": ("last_name", "lastname"),
}


def _clean(value: Any) -> Any:
    if pd.isna(value):
        return None
    if hasattr(value, "item"):
        return value.item()
    return value


def _text(value: Any) -> str | None:
    cleaned = _clean(value)
    if cleaned is None:
        return None
    return str(cleaned).strip() or None


def _source_name(path: Path) -> str:
    match = re.search(r"fc[\s_-]*(?:20)?(2[4-9])", path.stem, re.IGNORECASE)
    if match:
        return f"ea_fc{match.group(1)}"
    slug = re.sub(r"[^a-z0-9]+", "_", path.stem.lower()).strip("_")
    return f"ea_{slug}"


def _edition(value: Any, path: Path) -> str | None:
    cleaned = _text(value)
    if cleaned:
        match = re.search(r"(?:fc|fifa[\s_-]*)?(2[4-9])", cleaned, re.IGNORECASE)
        if match:
            return f"FC{match.group(1)}"
    source = _source_name(path)
    match = re.search(r"fc(2[4-9])", source, re.IGNORECASE)
    return f"FC{match.group(1)}" if match else None


def _columns(path: Path) -> dict[str, str]:
    available = {
        str(column).strip().lower(): str(column)
        for column in pd.read_csv(path, nrows=0).columns
    }
    selected: dict[str, str] = {}
    for field, aliases in COLUMN_ALIASES.items():
        for alias in aliases:
            if alias in available:
                selected[field] = available[alias]
                break
    if "name" not in selected:
        if "first_name" in selected and "last_name" in selected:
            selected["name"] = "__name_from_parts__"
        else:
            selected["name"] = ""
    missing = {"player_id", "name", "position", "overall"}.difference(selected)
    if not selected.get("name") and not (
        "first_name" in selected and "last_name" in selected
    ):
        missing.add("name (or first_name + last_name)")
    if missing:
        raise ValueError(
            f"{path.name}: não encontrei as colunas obrigatórias "
            f"{', '.join(sorted(missing))}. Colunas disponíveis: "
            f"{', '.join(available.values())}"
        )
    return selected


def _position_tokens(value: Any) -> list[str]:
    cleaned = _clean(value)
    if cleaned is None:
        return []
    return [
        token.strip().upper()
        for token in re.split(r"[,;/|\s]+", str(cleaned))
        if token.strip()
    ]


def inspect_positions(paths: list[Path]) -> dict[str, list[str]]:
    """Lista posições principais e alternativas distintas por arquivo."""
    result: dict[str, list[str]] = {}
    for path in paths:
        columns = _columns(path)
        for field in (
            "position",
            "alternate_position",
            "alternate_position_2",
            "alternate_position_3",
        ):
            column = columns.get(field)
            if column is None:
                continue
            values: set[str] = set()
            for chunk in pd.read_csv(path, usecols=[column], chunksize=5000):
                values.update(
                    chunk[column].dropna().astype(str).map(str.strip).tolist()
                )
            result[f"{path} [{column}]"] = sorted(values)
    return result


def _show_positions(positions: dict[str, list[str]]) -> None:
    print("Posições EA FC distintas encontradas:")
    for path, values in positions.items():
        print(f"- {path}: {', '.join(values) if values else '(nenhuma)'}")


def _unknown_position_tokens(positions: dict[str, list[str]]) -> dict[str, list[str]]:
    unknown: dict[str, list[str]] = {}
    for path, values in positions.items():
        tokens = {
            token
            for value in values
            for token in _position_tokens(value)
            if token not in POSITION_MAP
        }
        if tokens:
            unknown[path] = sorted(tokens)
    return unknown


def _overall(value: Any, path: Path, row_number: int) -> int | None:
    cleaned = _clean(value)
    if cleaned is None:
        return None
    try:
        numeric = float(cleaned)
    except (TypeError, ValueError) as error:
        raise ValueError(
            f"{path.name}, linha {row_number}: overall inválido: {cleaned!r}"
        ) from error
    if not numeric.is_integer():
        raise ValueError(
            f"{path.name}, linha {row_number}: overall não inteiro: {cleaned!r}"
        )
    result = int(numeric)
    if not 1 <= result <= 99:
        raise ValueError(
            f"{path.name}, linha {row_number}: overall fora da faixa 1-99: {result}"
        )
    return result


def _iter_rows(
    path: Path, columns: dict[str, str], limit: int | None
) -> Iterator[dict[str, Any]]:
    rows_read = 0
    with pd.read_csv(
        path,
        usecols=[
            value
            for key, value in columns.items()
            if not key.startswith("_") and value != "__name_from_parts__"
        ],
        dtype={columns["player_id"]: "string"},
        chunksize=5000,
    ) as reader:
        for chunk in reader:
            for row in chunk.to_dict(orient="records"):
                if limit is not None and rows_read >= limit:
                    return
                rows_read += 1
                yield row


def _snapshot_date(value: Any, path: Path) -> date | None:
    text = _text(value)
    if text:
        match = re.search(r"\d{4}-\d{2}-\d{2}", text)
        if match:
            try:
                return date.fromisoformat(match.group())
            except ValueError:
                pass
    match = re.search(r"20\d{6}", path.stem)
    if match:
        try:
            return date(
                int(match.group()[:4]), int(match.group()[4:6]), int(match.group()[6:])
            )
        except ValueError:
            return None
    match = re.search(r"20\d{2}-\d{2}-\d{2}", path.stem)
    if match:
        try:
            return date.fromisoformat(match.group())
        except ValueError:
            return None
    return None


def _age_at(birth_date: str | None, on_date: date | None) -> int | None:
    if not birth_date or on_date is None:
        return None
    try:
        born = date.fromisoformat(birth_date[:10])
    except ValueError:
        return None
    return (
        on_date.year
        - born.year
        - ((on_date.month, on_date.day) < (born.month, born.day))
    )


def _player_candidates(
    identities_by_name: dict[str, list[dict[str, Any]]],
    name: str,
    birth_date: str | None,
    nationality: str | None,
    age: int | None,
    snapshot: date | None,
) -> list[dict[str, Any]]:
    normalized_name = normalize_player_name(name)
    same_name = identities_by_name.get(normalized_name, [])
    if birth_date:
        target_birth = birth_date[:10]
        return [
            row
            for row in same_name
            if row["birth_date"] and str(row["birth_date"])[:10] == target_birth
        ]
    if age is None or snapshot is None or not nationality:
        return []
    normalized_nationality = normalize_player_name(nationality)
    return [
        row
        for row in same_name
        if row["nationality"]
        and normalize_player_name(row["nationality"]) == normalized_nationality
        and _age_at(row["birth_date"], snapshot) is not None
        and abs(_age_at(row["birth_date"], snapshot) - age) <= 1
    ]


def _is_brazilian_team(
    club: str | None, league: str | None, league_country: str | None = None
) -> bool:
    text = normalize_player_name(f"{club or ''} {league or ''} {league_country or ''}")
    return bool(
        re.search(
            r"\b(brasil|brazil|brasileirao|serie a brazil|serie b brazil|"
            r"campeonato brasileiro|brasileiro serie)\b",
            text,
        )
    )


def _edition_rank(edition: str | None) -> int:
    match = re.search(r"FC(\d{2})", edition or "", re.IGNORECASE)
    return int(match.group(1)) if match else 0


def _review_record(
    source: str,
    source_id: str,
    name: str,
    birth_date: str | None,
    nationality: str | None,
    club: str | None,
    league: str | None,
    candidates: list[dict[str, Any]],
    reason: str,
) -> dict[str, Any]:
    return {
        "source": source,
        "source_id": source_id,
        "name": name,
        "birth_date": birth_date or "",
        "nationality": nationality or "",
        "club": club or "",
        "league": league or "",
        "candidate_player_ids": "|".join(str(row["id"]) for row in candidates),
        "candidate_names": "|".join(str(row["name"]) for row in candidates),
        "reason": reason,
    }


def _import_file(
    connection,
    path: Path,
    columns: dict[str, str],
    limit: int | None,
    review_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    edition = _edition(None, path)
    source = f"ea_fc{edition[2:].lower()}" if edition else _source_name(path)
    imported = matched = ambiguous = new_players = brazil_excluded = 0
    missing_position = 0
    identity_rows = [dict(row) for row in connection.execute("""
            SELECT id, name, birth_date, nationality, tm_player_id, overall_edition,
                   overall_source
            FROM players
            ORDER BY id
            """)]
    identities_by_name: dict[str, list[dict[str, Any]]] = {}
    for identity in identity_rows:
        identities_by_name.setdefault(
            normalize_player_name(identity["name"]), []
        ).append(identity)

    for row_number, row in enumerate(_iter_rows(path, columns, limit), start=2):
        source_id = _clean(row.get(columns["player_id"]))
        name_column = columns["name"]
        name = (
            None
            if name_column == "__name_from_parts__"
            else _text(row.get(name_column))
        )
        if not name and "first_name" in columns and "last_name" in columns:
            first_name = _text(row.get(columns["first_name"]))
            last_name = _text(row.get(columns["last_name"]))
            name = " ".join(part for part in (first_name, last_name) if part)
        if source_id is None or name is None or not str(name).strip():
            continue
        source_id = str(source_id).strip()
        if not source_id:
            continue
        name = str(name).strip()
        raw_position = _clean(row.get(columns["position"]))
        tokens = _position_tokens(raw_position)
        if not tokens:
            missing_position += 1
        game_position = POSITION_MAP.get(tokens[0]) if tokens else None
        overall = _overall(row.get(columns["overall"]), path, row_number)
        row_edition = _edition(row.get(columns.get("edition", "")), path)
        edition = row_edition or edition
        source = f"ea_fc{edition[2:].lower()}" if edition else _source_name(path)
        source_club = _text(row.get(columns.get("club", "")))
        source_league = _text(row.get(columns.get("league", "")))
        league_country = _text(row.get(columns.get("league_country", "")))
        birth_date = _text(row.get(columns.get("birth_date", "")))
        nationality = _text(row.get(columns.get("nationality", "")))
        photo_url = _text(row.get(columns.get("photo", "")))
        snapshot = _snapshot_date(row.get(columns.get("source_date", "")), path)
        source_snapshot_date = snapshot.isoformat() if snapshot else None
        raw_age = _clean(row.get(columns.get("age", "")))
        try:
            age = int(float(raw_age)) if raw_age is not None else None
        except (TypeError, ValueError):
            age = None
        existing = connection.execute(
            "SELECT player_id FROM player_sources WHERE source = ? AND source_id = ?",
            (source, source_id),
        ).fetchone()
        candidates = _player_candidates(
            identities_by_name, name, birth_date, nationality, age, snapshot
        )
        same_name = identities_by_name.get(normalize_player_name(name), [])
        if existing:
            candidates = [
                identity
                for identity in candidates
                if identity["id"] == existing["player_id"]
            ]
            if not candidates:
                candidates = [
                    identity
                    for identity in identity_rows
                    if identity["id"] == existing["player_id"]
                ]
        same_name_missing_birth = any(
            not identity["birth_date"] for identity in same_name
        )
        if len(candidates) > 1:
            ambiguous += 1
            review_rows.append(
                _review_record(
                    source,
                    source_id,
                    name,
                    birth_date,
                    nationality,
                    source_club,
                    source_league,
                    candidates,
                    "multiple_identity_matches",
                )
            )
            continue
        if len(candidates) == 0 and (
            same_name_missing_birth
            or (not birth_date and (age is None or snapshot is None or not nationality))
        ):
            review_rows.append(
                _review_record(
                    source,
                    source_id,
                    name,
                    birth_date,
                    nationality,
                    source_club,
                    source_league,
                    candidates,
                    "insufficient_identity_data",
                )
            )
            ambiguous += 1
            continue

        is_new_player = not candidates
        if is_new_player and _is_brazilian_team(
            source_club, source_league, league_country
        ):
            brazil_excluded += 1
            continue

        if is_new_player:
            player_id = connection.execute(
                """
                INSERT INTO players (
                    name, birth_date, nationality, club_name, original_position,
                    game_position, overall, overall_source, overall_edition,
                    activity_status, phase2_candidate, needs_position_review,
                    photo_url
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'unknown', 1, ?, ?)
                """,
                (
                    name,
                    birth_date,
                    nationality,
                    source_club,
                    str(raw_position) if raw_position is not None else None,
                    game_position,
                    overall,
                    "EA" if overall is not None else None,
                    edition,
                    int(not tokens),
                    photo_url,
                ),
            ).lastrowid
            new_players += 1
        else:
            player_id = candidates[0]["id"]
            matched += 1
            current = connection.execute(
                """
                SELECT overall_source, overall_edition
                FROM players WHERE id = ?
                """,
                (player_id,),
            ).fetchone()
            current_source_rank = (
                _edition_rank(current["overall_edition"])
                if current["overall_source"]
                and current["overall_source"].casefold() in {"ea", source}
                else 0
            )
            current_source_date = connection.execute(
                """
                SELECT MAX(source_snapshot_date) AS snapshot_date
                FROM player_sources
                WHERE player_id = ? AND source LIKE 'ea_fc%'
                """,
                (player_id,),
            ).fetchone()["snapshot_date"]
            incoming_rank = _edition_rank(edition)
            incoming_is_newer = incoming_rank > current_source_rank or (
                incoming_rank == current_source_rank
                and (source_snapshot_date or "") >= (current_source_date or "")
            )
            if incoming_is_newer:
                connection.execute(
                    """
                    UPDATE players SET
                        name = ?, birth_date = COALESCE(?, birth_date),
                        nationality = COALESCE(?, nationality),
                        club_name = CASE WHEN tm_player_id IS NULL
                            THEN COALESCE(?, club_name) ELSE club_name END,
                        original_position = ?, game_position = ?, overall = ?,
                        overall_source = ?, overall_edition = ?,
                        needs_position_review = ?, photo_url = COALESCE(?, photo_url),
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (
                        name,
                        birth_date,
                        nationality,
                        source_club,
                        str(raw_position) if raw_position is not None else None,
                        game_position,
                        overall,
                        "EA" if overall is not None else None,
                        edition,
                        int(not tokens),
                        photo_url,
                        player_id,
                    ),
                )

        connection.execute(
            """
            INSERT INTO player_sources (
                source, source_id, player_id, source_name, source_club_name,
                source_position, source_league_name, source_snapshot_date
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source, source_id) DO UPDATE SET
                player_id = excluded.player_id,
                source_name = excluded.source_name,
                source_club_name = excluded.source_club_name,
                source_position = excluded.source_position,
                source_league_name = excluded.source_league_name,
                source_snapshot_date = excluded.source_snapshot_date
            """,
            (
                source,
                source_id,
                player_id,
                name,
                source_club,
                str(raw_position) if raw_position is not None else None,
                source_league,
                source_snapshot_date,
            ),
        )
        if is_new_player:
            identity = {
                "id": player_id,
                "name": name,
                "birth_date": birth_date,
                "nationality": nationality,
                "tm_player_id": None,
                "overall_edition": edition,
                "overall_source": "EA",
            }
            identity_rows.append(identity)
            identities_by_name.setdefault(normalize_player_name(name), []).append(
                identity
            )
        imported += 1

    return {
        "source": source,
        "players": imported,
        "missing_position": missing_position,
        "matched": matched,
        "ambiguous": ambiguous,
        "new_players": new_players,
        "brazil_excluded": brazil_excluded,
    }


def _copy_database_read_only(source_path: Path, target_path: Path) -> None:
    if not source_path.is_file():
        raise FileNotFoundError(f"Banco SQLite não encontrado: {source_path}")
    source = sqlite3.connect(
        f"file:{source_path.resolve().as_posix()}?mode=ro", uri=True
    )
    target = sqlite3.connect(target_path)
    try:
        source.backup(target)
        target.commit()
    finally:
        target.close()
        source.close()


def _display_name_from_row(row: dict[str, Any], columns: dict[str, str]) -> str | None:
    selected = columns["name"]
    name = None if selected == "__name_from_parts__" else _text(row.get(selected))
    if not name and "first_name" in columns and "last_name" in columns:
        first = _text(row.get(columns["first_name"]))
        last = _text(row.get(columns["last_name"]))
        name = " ".join(part for part in (first, last) if part) or None
    return name


def _source_id(value: Any) -> str | None:
    cleaned = _clean(value)
    if cleaned is None:
        return None
    result = str(cleaned).strip()
    if result.endswith(".0") and result[:-2].isdigit():
        result = result[:-2]
    return result or None


def simulate_ea_import(
    paths: list[Path],
    database_path: str | Path = DATABASE_PATH,
    limit: int | None = None,
    review_path: str | Path | None = None,
) -> dict[str, Any]:
    """Simula importação em cópia isolada; o banco informado é somente leitura."""
    if not paths:
        raise ValueError("Nenhum CSV EA FC foi informado.")
    if limit is not None and limit < 1:
        raise ValueError("--limit deve ser um inteiro maior que zero.")

    columns_by_path = {path: _columns(path) for path in paths}
    for path, columns in columns_by_path.items():
        missing = {"birth_date", "age"}.intersection(columns)
        if not missing:
            print(f"{path.name}: sem coluna de data de nascimento ou idade.")

    if review_path is None:
        review_path = ROOT / "data" / "review" / "ea_matches_dry_run.csv"
    output_review = Path(review_path)

    with tempfile.TemporaryDirectory(prefix="memi-ea-dry-run-") as temporary:
        simulated_database = Path(temporary) / "memi-simulation.sqlite"
        _copy_database_read_only(Path(database_path), simulated_database)

        from src.overall import estimate_overall

        with connect(simulated_database) as connection:
            market_overalls = {
                row["id"]: (
                    estimate_overall(row["market_value_eur"])
                    if row["market_value_eur"] is not None
                    else None
                )
                for row in connection.execute(
                    "SELECT id, market_value_eur FROM players"
                )
            }

        import_results = import_ea(
            paths,
            database_path=simulated_database,
            limit=limit,
            show_positions=False,
            review_path=output_review,
        )

        with connect(simulated_database) as connection:
            imported_sources = {
                (row["source"], row["source_id"]): dict(row)
                for row in connection.execute("""
                    SELECT ps.source, ps.source_id, p.id AS player_id,
                           p.tm_player_id, p.display_name, p.name, p.overall
                    FROM player_sources AS ps
                    JOIN players AS p ON p.id = ps.player_id
                    WHERE ps.source LIKE 'ea_fc%'
                    """)
            }

        review_entries: dict[tuple[str, str], dict[str, str]] = {}
        if output_review.is_file():
            with output_review.open(encoding="utf-8-sig", newline="") as file:
                for entry in csv.DictReader(file):
                    review_entries[(entry["source"], entry["source_id"])] = entry

        comparisons: list[tuple[int, int, int]] = []
        top_players: list[dict[str, Any]] = []
        file_summaries: list[dict[str, Any]] = []
        for path in paths:
            columns = columns_by_path[path]
            frame = pd.read_csv(path)
            birth_column = columns.get("birth_date")
            age_column = columns.get("age")
            birth_count = (
                int(frame[birth_column].notna().sum())
                if birth_column is not None
                else 0
            )
            age_count = (
                int(frame[age_column].notna().sum()) if age_column is not None else 0
            )
            editions = (
                sorted(frame[columns["edition"]].dropna().astype(str).unique())
                if columns.get("edition")
                else []
            )
            snapshots = (
                sorted(frame[columns["source_date"]].dropna().astype(str).unique())
                if columns.get("source_date")
                else []
            )
            file_summaries.append(
                {
                    "path": path,
                    "columns": list(frame.columns),
                    "birth_date_column": birth_column,
                    "birth_date_count": birth_count,
                    "age_column": age_column,
                    "age_count": age_count,
                    "editions": editions,
                    "snapshots": snapshots,
                    "rows": len(frame),
                }
            )

            def record_identity(row: dict[str, Any]) -> tuple[str, str] | None:
                source_id = _source_id(row.get(columns["player_id"]))
                edition = _edition(row.get(columns.get("edition", "")), path)
                source = (
                    f"ea_fc{edition[2:].lower()}" if edition else _source_name(path)
                )
                if source_id is None:
                    return None
                return source, source_id

            ordered = frame.sort_values(
                by=columns["overall"], ascending=False, kind="stable"
            ).head(30)
            for source_row in ordered.to_dict(orient="records"):
                identity = record_identity(source_row)
                if identity is None:
                    continue
                imported = imported_sources.get(identity)
                review = review_entries.get(identity)
                club = _text(source_row.get(columns.get("club", "")))
                league = _text(source_row.get(columns.get("league", "")))
                league_country = _text(
                    source_row.get(columns.get("league_country", ""))
                )
                if review:
                    status = f"ambíguo: {review['candidate_names'] or review['reason']}"
                elif imported and imported["tm_player_id"] is not None:
                    status = f"casado TM {imported['tm_player_id']}"
                elif imported:
                    status = "entraria como novo"
                elif _is_brazilian_team(club, league, league_country):
                    status = "excluído: clube/liga brasileira"
                else:
                    status = "não importado (linha inválida ou fora do limite)"
                top_players.append(
                    {
                        "name": _display_name_from_row(source_row, columns)
                        or "(sem nome)",
                        "overall": source_row.get(columns["overall"]),
                        "club": club or "(sem clube)",
                        "source_id": identity[1],
                        "status": status,
                    }
                )

        for (source, source_id), imported in imported_sources.items():
            if imported["tm_player_id"] is None:
                continue
            market_overall = market_overalls.get(imported["player_id"])
            ea_overall = imported["overall"]
            if market_overall is not None and ea_overall is not None:
                comparisons.append(
                    (market_overall, ea_overall, ea_overall - market_overall)
                )

        correlation = (
            float(
                pd.Series([entry[0] for entry in comparisons]).corr(
                    pd.Series([entry[1] for entry in comparisons])
                )
            )
            if len(comparisons) >= 2
            else None
        )
        average_difference = (
            sum(entry[2] for entry in comparisons) / len(comparisons)
            if comparisons
            else None
        )
        matched_total = sum(counts["matched"] for counts in import_results.values())
        matched_without_market_value = matched_total - len(comparisons)

    for summary in file_summaries:
        print(f"Arquivo: {summary['path']}")
        print(f"Colunas ({len(summary['columns'])}): {', '.join(summary['columns'])}")
        if summary["birth_date_column"]:
            print(
                f"Data de nascimento: {summary['birth_date_column']} "
                f"({summary['birth_date_count']:,} preenchidos); "
                f"idade: {summary['age_column'] or 'sem coluna'} "
                f"({summary['age_count']:,} preenchidos)"
            )
        else:
            print(
                f"Sem data de nascimento; idade: {summary['age_column'] or 'sem coluna'} "
                f"({summary['age_count']:,} preenchidos)"
            )
        print(f"Edições: {', '.join(summary['editions']) or '(sem coluna)'}")
        print(f"Snapshots: {', '.join(summary['snapshots']) or '(sem coluna)'}")
        print(f"Linhas no arquivo: {summary['rows']:,}")

    print("Resultado simulado (o banco original não foi alterado):")
    for filename, counts in import_results.items():
        print(
            f"{filename}: {counts['matched']:,} casados com TM; "
            f"{counts['ambiguous']:,} ambíguos; "
            f"{counts['new_players']:,} novos; "
            f"{counts['brazil_excluded']:,} excluídos por clube/liga brasileira."
        )
    print(
        f"Correlação de Pearson EA vs. estimativa de mercado: " f"{correlation:.4f}"
        if correlation is not None
        else "Correlação de Pearson EA vs. estimativa de mercado: indefinida."
    )
    print(
        f"Jogadores comparados: {len(comparisons):,}; diferença média "
        f"(EA - mercado): {average_difference:+.2f} pontos."
        if average_difference is not None
        else "Jogadores comparados: 0; diferença média indisponível."
    )
    print(
        f"Casados sem valor de mercado para comparar: "
        f"{matched_without_market_value:,}."
    )
    print(f"CSV de revisão (simulação): {output_review}")
    print("Top 30 overalls do EA e resultado do cruzamento:")
    for player in top_players[:30]:
        print(
            f"  {player['overall']:>2} | {player['name']} | {player['club']} "
            f"| EA ID {player['source_id']} | {player['status']}"
        )
    return {
        "imports": import_results,
        "file_summaries": file_summaries,
        "comparisons": len(comparisons),
        "matched_without_market_value": matched_without_market_value,
        "correlation": correlation,
        "average_difference": average_difference,
        "review_path": output_review,
        "top_players": top_players[:30],
    }


def import_ea(
    paths: list[Path],
    database_path: str | Path = DATABASE_PATH,
    limit: int | None = None,
    show_positions: bool = True,
    review_path: str | Path | None = None,
) -> dict[str, dict[str, Any]]:
    """Importa arquivos EA como fontes separadas, sem merge automático."""
    if not paths:
        raise ValueError("Nenhum CSV EA FC foi informado.")
    if limit is not None and limit < 1:
        raise ValueError("--limit deve ser um inteiro maior que zero.")

    positions = inspect_positions(paths)
    if show_positions:
        _show_positions(positions)
    unknown = _unknown_position_tokens(positions)
    if unknown:
        details = "; ".join(
            f"{Path(path).name}: {', '.join(tokens)}"
            for path, tokens in unknown.items()
        )
        raise ValueError(
            "Há posições EA FC sem mapeamento; revise antes de importar: " + details
        )

    columns_by_path = {path: _columns(path) for path in paths}
    initialize_database(database_path)
    result: dict[str, dict[str, Any]] = {}
    review_rows: list[dict[str, Any]] = []
    with connect(database_path) as connection:
        for path in paths:
            counts = _import_file(
                connection, path, columns_by_path[path], limit, review_rows
            )
            result[path.name] = counts
            connection.execute(
                """
                INSERT INTO ea_import_stats (
                    source, matched, ambiguous, new_players, brazil_excluded
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(source) DO UPDATE SET
                    matched = excluded.matched,
                    ambiguous = excluded.ambiguous,
                    new_players = excluded.new_players,
                    brazil_excluded = excluded.brazil_excluded,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    counts["source"],
                    counts["matched"],
                    counts["ambiguous"],
                    counts["new_players"],
                    counts["brazil_excluded"],
                ),
            )
        refresh_display_names(connection)
    if review_path is None:
        review_path = ROOT / "data" / "review" / "ea_matches.csv"
    review_file = Path(review_path)
    review_file.parent.mkdir(parents=True, exist_ok=True)
    with review_file.open("w", encoding="utf-8-sig", newline="") as output:
        writer = csv.DictWriter(
            output,
            fieldnames=(
                "source",
                "source_id",
                "name",
                "birth_date",
                "nationality",
                "club",
                "league",
                "candidate_player_ids",
                "candidate_names",
                "reason",
            ),
        )
        writer.writeheader()
        writer.writerows(review_rows)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        type=Path,
        action="append",
        dest="inputs",
        help="CSV EA FC (repita para importar mais de um; padrão: CSVs em data/raw)",
    )
    parser.add_argument("--database", type=Path, default=DATABASE_PATH)
    parser.add_argument(
        "--limit",
        type=int,
        help="limita linhas importadas por arquivo para teste",
    )
    parser.add_argument(
        "--show-positions",
        action="store_true",
        help="lista colunas e posições distintas sem importar",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="simula a importação em cópia temporária, sem alterar o banco",
    )
    parser.add_argument(
        "--review-output",
        type=Path,
        help="caminho do CSV de revisão (padrão: data/review/ea_matches_dry_run.csv)",
    )
    args = parser.parse_args()
    paths = args.inputs or sorted(RAW_DIRECTORY.glob("*.csv"))

    try:
        if args.show_positions:
            if not paths:
                parser.error(
                    f"Nenhum CSV encontrado em {RAW_DIRECTORY}; informe um com --input."
                )
            _show_positions(inspect_positions(paths))
            return
        if args.dry_run:
            simulate_ea_import(
                paths,
                database_path=args.database,
                limit=args.limit,
                review_path=args.review_output,
            )
            return
        result = import_ea(
            paths,
            database_path=args.database,
            limit=args.limit,
            review_path=args.review_output,
        )
    except (OSError, ValueError, KeyError, pd.errors.ParserError) as error:
        parser.error(str(error))

    for filename, counts in result.items():
        print(
            f"{filename}: {counts['players']:,} jogadores importados; "
            f"{counts['matched']:,} casados, {counts['ambiguous']:,} para revisão, "
            f"{counts['new_players']:,} novos, "
            f"{counts['brazil_excluded']:,} novos do Brasil ignorados, "
            f"{counts['missing_position']:,} sem posição."
        )


if __name__ == "__main__":
    main()
