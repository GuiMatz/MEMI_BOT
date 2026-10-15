"""Importa datasets EA FC para o SQLite sem unir jogadores entre fontes."""

from __future__ import annotations

import argparse
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pandas as pd

from src.db import DATABASE_PATH, connect, initialize_database

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
    "player_id": ("player_id", "sofifa_id", "ea_id", "id"),
    "name": ("long_name", "player_name", "name", "short_name"),
    "position": ("player_positions", "positions", "player_position", "position"),
    "overall": ("overall", "overall_rating", "rating", "ovr"),
    "birth_date": ("dob", "date_of_birth", "birth_date", "birthdate"),
    "nationality": ("nationality_name", "nationality", "nation", "country"),
    "club": ("club_name", "team_name", "club", "team"),
    "photo": ("player_face_url", "photo_url", "image_url", "photo"),
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
    match = re.search(r"fc[\s_-]*(?:20)?(24|25)", path.stem, re.IGNORECASE)
    if match:
        return f"ea_fc{match.group(1)}"
    slug = re.sub(r"[^a-z0-9]+", "_", path.stem.lower()).strip("_")
    return f"ea_{slug}"


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
    missing = {"player_id", "name", "position", "overall"}.difference(selected)
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
        for token in re.split(r"[,;/|]", str(cleaned))
        if token.strip()
    ]


def inspect_positions(paths: list[Path]) -> dict[str, list[str]]:
    """Lista posições originais distintas e tokens individuais por arquivo."""
    result: dict[str, list[str]] = {}
    for path in paths:
        column = _columns(path)["position"]
        values: set[str] = set()
        for chunk in pd.read_csv(path, usecols=[column], chunksize=5000):
            values.update(chunk[column].dropna().astype(str).map(str.strip).tolist())
        result[str(path)] = sorted(values)
    return result


def _show_positions(positions: dict[str, list[str]]) -> None:
    print("Posições EA FC distintas encontradas:")
    for path, values in positions.items():
        print(f"- {Path(path).name}: {', '.join(values) if values else '(nenhuma)'}")


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
        usecols=list(columns.values()),
        dtype={columns["player_id"]: "string"},
        chunksize=5000,
    ) as reader:
        for chunk in reader:
            for row in chunk.to_dict(orient="records"):
                if limit is not None and rows_read >= limit:
                    return
                rows_read += 1
                yield row


def _import_file(
    connection,
    path: Path,
    columns: dict[str, str],
    limit: int | None,
) -> dict[str, int]:
    source = _source_name(path)
    imported = 0
    missing_position = 0

    for row_number, row in enumerate(_iter_rows(path, columns, limit), start=2):
        source_id = _clean(row.get(columns["player_id"]))
        name = _clean(row.get(columns["name"]))
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
        source_club = _text(row.get(columns.get("club", "")))
        birth_date = _text(row.get(columns.get("birth_date", "")))
        nationality = _text(row.get(columns.get("nationality", "")))
        photo_url = _text(row.get(columns.get("photo", "")))

        existing = connection.execute(
            "SELECT player_id FROM player_sources WHERE source = ? AND source_id = ?",
            (source, source_id),
        ).fetchone()
        if existing:
            player_id = existing["player_id"]
            connection.execute(
                """
                UPDATE players SET
                    name = ?, birth_date = ?, nationality = ?, game_position = ?,
                    overall = ?, overall_source = ?, photo_url = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    name,
                    birth_date,
                    nationality,
                    game_position,
                    overall,
                    source if overall is not None else None,
                    photo_url,
                    player_id,
                ),
            )
            connection.execute(
                """
                UPDATE player_sources SET source_name = ?, source_club_name = ?,
                    source_position = ?
                WHERE source = ? AND source_id = ?
                """,
                (
                    name,
                    source_club,
                    str(raw_position) if raw_position is not None else None,
                    source,
                    source_id,
                ),
            )
        else:
            player_id = connection.execute(
                """
                INSERT INTO players (
                    name, birth_date, nationality, game_position, overall,
                    overall_source, photo_url
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    name,
                    birth_date,
                    nationality,
                    game_position,
                    overall,
                    source if overall is not None else None,
                    photo_url,
                ),
            ).lastrowid
            connection.execute(
                """
                INSERT INTO player_sources (
                    source, source_id, player_id, source_name,
                    source_club_name, source_position
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    source,
                    source_id,
                    player_id,
                    name,
                    source_club,
                    str(raw_position) if raw_position is not None else None,
                ),
            )
        imported += 1

    return {"players": imported, "missing_position": missing_position}


def import_ea(
    paths: list[Path],
    database_path: str | Path = DATABASE_PATH,
    limit: int | None = None,
    show_positions: bool = True,
) -> dict[str, dict[str, int]]:
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
    result: dict[str, dict[str, int]] = {}
    with connect(database_path) as connection:
        for path in paths:
            result[path.name] = _import_file(
                connection, path, columns_by_path[path], limit
            )
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
        result = import_ea(
            paths,
            database_path=args.database,
            limit=args.limit,
        )
    except (OSError, ValueError, KeyError, pd.errors.ParserError) as error:
        parser.error(str(error))

    for filename, counts in result.items():
        print(
            f"{filename}: {counts['players']:,} jogadores importados; "
            f"{counts['missing_position']:,} sem posição."
        )


if __name__ == "__main__":
    main()
