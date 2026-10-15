"""Importa jogadores e clubes dos CSVs públicos do Transfermarkt."""

from __future__ import annotations

import argparse
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import pandas as pd

from src.db import DATABASE_PATH, connect, initialize_database

ROOT = Path(__file__).resolve().parents[1]
PLAYERS_PATH = ROOT / "data" / "raw" / "players.csv.gz"
CLUBS_PATH = ROOT / "data" / "raw" / "clubs.csv.gz"
SOURCE = "transfermarkt"

POSITION_MAP = {
    "Attacking Midfield": "MEI",
    "Central Midfield": "MC",
    "Centre-Back": "ZAG",
    "Centre-Forward": "CA",
    "Defensive Midfield": "MC",
    "Goalkeeper": "GOL",
    "Left Midfield": "PE",
    "Left Winger": "PE",
    "Left-Back": "LE",
    "Right Midfield": "PD",
    "Right Winger": "PD",
    "Right-Back": "LD",
    "Second Striker": "CA",
}

GENERAL_POSITION_MAP = {
    "Attack": "CA",
    "Defender": "ZAG",
    "Goalkeeper": "GOL",
    "Midfield": "MC",
}


def _clean(value: Any) -> Any:
    """Converte valores ausentes do pandas em None e escalares numpy em Python."""
    if pd.isna(value):
        return None
    if hasattr(value, "item"):
        return value.item()
    return value


def _identifier(value: Any) -> str | None:
    cleaned = _clean(value)
    if cleaned is None:
        return None
    return str(cleaned).strip() or None


def distinct_positions(players_path: str | Path = PLAYERS_PATH) -> dict[str, list[str]]:
    """Retorna as categorias completas das colunas position e sub_position."""
    categories: dict[str, list[str]] = {}
    for column in ("position", "sub_position"):
        values = pd.read_csv(
            players_path,
            compression="gzip",
            usecols=[column],
        )[column]
        categories[column] = sorted(values.dropna().astype(str).unique().tolist())
    return categories


def _show_positions(categories: dict[str, list[str]]) -> None:
    print("Valores distintos encontrados no Transfermarkt:")
    for column, values in categories.items():
        print(f"- {column}: {', '.join(values) if values else '(nenhum)'}")


def _upsert_league(connection, competition_id: str | None) -> int | None:
    if competition_id is None:
        return None
    connection.execute(
        """
        INSERT INTO leagues (name, transfermarkt_id)
        VALUES (?, ?)
        ON CONFLICT(transfermarkt_id) DO NOTHING
        """,
        (competition_id, competition_id),
    )
    return connection.execute(
        "SELECT id FROM leagues WHERE transfermarkt_id = ?",
        (competition_id,),
    ).fetchone()["id"]


def _upsert_stadium(connection, name: str | None, seats: Any) -> int | None:
    if name is None:
        return None
    existing = connection.execute(
        "SELECT id FROM stadiums WHERE name = ? LIMIT 1", (name,)
    ).fetchone()
    capacity = _clean(seats)
    if capacity is not None:
        capacity = int(capacity)
    if existing:
        connection.execute(
            "UPDATE stadiums SET capacity = COALESCE(?, capacity) WHERE id = ?",
            (capacity, existing["id"]),
        )
        return existing["id"]
    return connection.execute(
        "INSERT INTO stadiums (name, capacity) VALUES (?, ?)",
        (name, capacity),
    ).lastrowid


def _upsert_coach(connection, club_id: int, name: str | None) -> int | None:
    if name is None:
        return None
    existing = connection.execute(
        "SELECT id FROM coaches WHERE club_id = ? ORDER BY id LIMIT 1",
        (club_id,),
    ).fetchone()
    if existing:
        connection.execute(
            "UPDATE coaches SET name = ? WHERE id = ?",
            (name, existing["id"]),
        )
        return existing["id"]
    return connection.execute(
        "INSERT INTO coaches (name, club_id) VALUES (?, ?)",
        (name, club_id),
    ).lastrowid


def _import_clubs(connection, clubs_path: str | Path) -> int:
    clubs = pd.read_csv(clubs_path, compression="gzip", dtype={"club_id": "string"})
    required = {"club_id", "name", "domestic_competition_id"}
    missing = required.difference(clubs.columns)
    if missing:
        raise ValueError(
            f"Colunas ausentes em clubs.csv.gz: {', '.join(sorted(missing))}"
        )

    imported = 0
    for row in clubs.to_dict(orient="records"):
        transfermarkt_id = _identifier(row["club_id"])
        name = _identifier(row["name"])
        if transfermarkt_id is None or name is None:
            continue

        competition_id = _identifier(row["domestic_competition_id"])
        league_id = _upsert_league(connection, competition_id)
        stadium_id = _upsert_stadium(
            connection,
            _identifier(row.get("stadium_name")),
            row.get("stadium_seats"),
        )
        connection.execute(
            """
            INSERT INTO clubs (name, league_id, stadium_id, transfermarkt_id)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(transfermarkt_id) DO UPDATE SET
                name = excluded.name,
                league_id = excluded.league_id,
                stadium_id = excluded.stadium_id
            """,
            (name, league_id, stadium_id, transfermarkt_id),
        )
        club_id = connection.execute(
            "SELECT id FROM clubs WHERE transfermarkt_id = ?",
            (transfermarkt_id,),
        ).fetchone()["id"]

        coach_id = _upsert_coach(
            connection, club_id, _identifier(row.get("coach_name"))
        )
        if coach_id is not None:
            connection.execute(
                "UPDATE clubs SET coach_id = ? WHERE id = ?", (coach_id, club_id)
            )
        imported += 1
    return imported


def _find_or_create_player(
    connection,
    source_id: str,
    name: str,
    birth_date: str | None,
    nationality: str | None,
    club_id: int | None,
    original_position: str | None,
    game_position: str | None,
    market_value: Any,
    photo_url: str | None,
    season_last_active: int | None,
    source_club_name: str | None,
    source_position: str | None,
    needs_position_review: bool,
) -> None:
    source_row = connection.execute(
        "SELECT player_id FROM player_sources WHERE source = ? AND source_id = ?",
        (SOURCE, source_id),
    ).fetchone()
    value = _clean(market_value)
    if value is not None:
        value = int(value)
    if source_row:
        player_id = source_row["player_id"]
        connection.execute(
            """
            UPDATE players SET
                name = ?, birth_date = ?, nationality = ?, club_id = ?,
                original_position = ?,
                game_position = CASE WHEN EXISTS (
                    SELECT 1 FROM player_sources
                    WHERE player_sources.player_id = players.id
                      AND player_sources.source LIKE 'ea_fc%'
                ) THEN game_position ELSE ? END,
                needs_position_review = CASE WHEN EXISTS (
                    SELECT 1 FROM player_sources
                    WHERE player_sources.player_id = players.id
                      AND player_sources.source LIKE 'ea_fc%'
                ) THEN needs_position_review ELSE ? END,
                market_value_eur = ?, season_last_active = ?,
                photo_url = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                name,
                birth_date,
                nationality,
                club_id,
                original_position,
                game_position,
                int(needs_position_review),
                value,
                season_last_active,
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
            (name, source_club_name, source_position, SOURCE, source_id),
        )
        return

    player_id = connection.execute(
        """
        INSERT INTO players (
            name, birth_date, nationality, club_id, original_position,
            game_position, market_value_eur, season_last_active,
            needs_position_review, photo_url
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            name,
            birth_date,
            nationality,
            club_id,
            original_position,
            game_position,
            value,
            season_last_active,
            int(needs_position_review),
            photo_url,
        ),
    ).lastrowid
    connection.execute(
        """
        INSERT INTO player_sources (
            source, source_id, player_id, source_name, source_club_name,
            source_position
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            SOURCE,
            source_id,
            player_id,
            name,
            source_club_name,
            source_position,
        ),
    )


def _club_lookup(connection) -> dict[str, int]:
    return {
        row["transfermarkt_id"]: row["id"]
        for row in connection.execute(
            "SELECT id, transfermarkt_id FROM clubs WHERE transfermarkt_id IS NOT NULL"
        )
    }


def _iter_players(
    players_path: str | Path, limit: int | None
) -> Iterable[dict[str, Any]]:
    chunksize = 5000
    rows_read = 0
    with pd.read_csv(
        players_path,
        compression="gzip",
        dtype={"player_id": "string", "current_club_id": "string"},
        chunksize=chunksize,
    ) as reader:
        for chunk in reader:
            for row in chunk.to_dict(orient="records"):
                if limit is not None and rows_read >= limit:
                    return
                rows_read += 1
                yield row


def import_transfermarkt(
    players_path: str | Path = PLAYERS_PATH,
    clubs_path: str | Path = CLUBS_PATH,
    database_path: str | Path = DATABASE_PATH,
    limit: int | None = None,
    show_positions: bool = True,
) -> dict[str, int]:
    """Importa clubes e jogadores, podendo limitar a quantidade de jogadores."""
    if limit is not None and limit < 1:
        raise ValueError("--limit deve ser um inteiro maior que zero.")

    categories = distinct_positions(players_path)
    if show_positions:
        _show_positions(categories)
    unknown = sorted(set(categories["sub_position"]).difference(POSITION_MAP))
    if unknown:
        raise ValueError(
            "Há subposições sem mapeamento; revise antes de importar: "
            + ", ".join(unknown)
        )

    initialize_database(database_path)
    with connect(database_path) as connection:
        club_count = _import_clubs(connection, clubs_path)
        club_ids = _club_lookup(connection)
        player_count = 0
        missing_sub_position_count = 0
        position_fallback_count = 0

        for row in _iter_players(players_path, limit):
            source_id = _identifier(row.get("player_id"))
            name = _identifier(row.get("name"))
            if name is None:
                first_name = _identifier(row.get("first_name"))
                last_name = _identifier(row.get("last_name"))
                name = " ".join(part for part in (first_name, last_name) if part)
            if source_id is None or name is None:
                continue

            sub_position = _identifier(row.get("sub_position"))
            broad_position = _identifier(row.get("position"))
            if sub_position is None:
                missing_sub_position_count += 1
            fallback_position = GENERAL_POSITION_MAP.get(broad_position)
            game_position = POSITION_MAP.get(sub_position) or fallback_position
            if sub_position is None and fallback_position is not None:
                position_fallback_count += 1
            needs_position_review = sub_position is None
            last_season = _clean(row.get("last_season"))
            if last_season is not None:
                last_season = int(last_season)
            source_club_name = _identifier(row.get("current_club_name"))
            _find_or_create_player(
                connection=connection,
                source_id=source_id,
                name=name,
                birth_date=_identifier(row.get("date_of_birth")),
                nationality=_identifier(row.get("country_of_citizenship")),
                club_id=club_ids.get(_identifier(row.get("current_club_id"))),
                original_position=sub_position or broad_position,
                game_position=game_position,
                market_value=row.get("market_value_in_eur"),
                photo_url=_identifier(row.get("image_url")),
                season_last_active=last_season,
                source_club_name=source_club_name,
                source_position=sub_position or broad_position,
                needs_position_review=needs_position_review,
            )
            player_count += 1

    return {
        "clubs": club_count,
        "players": player_count,
        "missing_sub_position": missing_sub_position_count,
        "position_fallback": position_fallback_count,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--players", type=Path, default=PLAYERS_PATH)
    parser.add_argument("--clubs", type=Path, default=CLUBS_PATH)
    parser.add_argument("--database", type=Path, default=DATABASE_PATH)
    parser.add_argument(
        "--limit",
        type=int,
        help="importa no máximo esta quantidade de jogadores (clubes não limitados)",
    )
    parser.add_argument(
        "--show-positions",
        action="store_true",
        help="lista as posições distintas e encerra sem importar",
    )
    args = parser.parse_args()

    try:
        categories = distinct_positions(args.players)
        _show_positions(categories)
        if args.show_positions:
            return
        result = import_transfermarkt(
            players_path=args.players,
            clubs_path=args.clubs,
            database_path=args.database,
            limit=args.limit,
            show_positions=False,
        )
    except (OSError, ValueError, KeyError, pd.errors.ParserError) as error:
        parser.error(str(error))

    print(
        "Importação concluída: "
        f"{result['players']:,} jogadores, {result['clubs']:,} clubes; "
        f"{result['missing_sub_position']:,} sem subposição."
    )


if __name__ == "__main__":
    main()
