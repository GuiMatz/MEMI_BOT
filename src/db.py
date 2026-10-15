"""Schema e conexão SQLite do Bot Memi."""

from __future__ import annotations

import argparse
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from collections.abc import Iterator

ROOT = Path(__file__).resolve().parents[1]
DATABASE_PATH = ROOT / "data" / "memi.sqlite"
GAME_POSITIONS = ("GOL", "ZAG", "LD", "LE", "MC", "MEI", "PE", "PD", "CA")

SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS leagues (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    country TEXT,
    transfermarkt_id TEXT UNIQUE,
    api_football_id INTEGER UNIQUE
);

CREATE TABLE IF NOT EXISTS stadiums (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    city TEXT,
    country TEXT,
    capacity INTEGER CHECK (capacity IS NULL OR capacity >= 0),
    transfermarkt_id TEXT UNIQUE,
    api_football_id INTEGER UNIQUE,
    photo_url TEXT,
    photo_path TEXT
);

CREATE TABLE IF NOT EXISTS coaches (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    birth_date TEXT,
    nationality TEXT,
    club_id INTEGER REFERENCES clubs(id) ON DELETE SET NULL,
    transfermarkt_id TEXT UNIQUE,
    api_football_id INTEGER UNIQUE,
    photo_url TEXT,
    photo_path TEXT
);

CREATE TABLE IF NOT EXISTS clubs (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    country TEXT,
    league_id INTEGER REFERENCES leagues(id) ON DELETE SET NULL,
    stadium_id INTEGER REFERENCES stadiums(id) ON DELETE SET NULL,
    coach_id INTEGER REFERENCES coaches(id) ON DELETE SET NULL,
    transfermarkt_id TEXT UNIQUE,
    api_football_id INTEGER UNIQUE,
    logo_url TEXT,
    logo_path TEXT
);

CREATE TABLE IF NOT EXISTS players (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    birth_date TEXT,
    nationality TEXT,
    club_id INTEGER REFERENCES clubs(id) ON DELETE SET NULL,
    original_position TEXT,
    game_position TEXT CHECK (
        game_position IS NULL OR game_position IN
        ('GOL', 'ZAG', 'LD', 'LE', 'MC', 'MEI', 'PE', 'PD', 'CA')
    ),
    overall INTEGER CHECK (overall IS NULL OR overall BETWEEN 1 AND 99),
    overall_source TEXT,
    overall_edition TEXT,
    market_value_eur INTEGER CHECK (
        market_value_eur IS NULL OR market_value_eur >= 0
    ),
    season_last_active INTEGER,
    activity_status TEXT NOT NULL DEFAULT 'unknown'
        CHECK (activity_status IN ('active', 'inactive', 'unknown')),
    phase2_candidate INTEGER NOT NULL DEFAULT 1
        CHECK (phase2_candidate IN (0, 1)),
    needs_position_review INTEGER NOT NULL DEFAULT 0
        CHECK (needs_position_review IN (0, 1)),
    photo_url TEXT,
    photo_path TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS player_sources (
    id INTEGER PRIMARY KEY,
    source TEXT NOT NULL,
    source_id TEXT NOT NULL,
    player_id INTEGER NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    source_name TEXT,
    source_club_name TEXT,
    source_position TEXT,
    source_season INTEGER,
    UNIQUE (source, source_id)
);

CREATE TABLE IF NOT EXISTS overall_adjustments (
    player_id INTEGER PRIMARY KEY REFERENCES players(id) ON DELETE CASCADE,
    overall INTEGER NOT NULL CHECK (overall BETWEEN 1 AND 99),
    reason TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS league_overall_weights (
    league_id INTEGER PRIMARY KEY REFERENCES leagues(id) ON DELETE CASCADE,
    weight REAL NOT NULL DEFAULT 1.0 CHECK (weight BETWEEN 0.5 AND 1.5),
    reason TEXT,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS player_match_candidates (
    id INTEGER PRIMARY KEY,
    player_a_id INTEGER NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    player_b_id INTEGER NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    source_a TEXT NOT NULL,
    source_b TEXT NOT NULL,
    name_similarity REAL NOT NULL CHECK (name_similarity BETWEEN 0 AND 100),
    birth_date_match INTEGER NOT NULL CHECK (birth_date_match IN (0, 1)),
    club_match INTEGER NOT NULL CHECK (club_match IN (0, 1)),
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'accepted', 'rejected')),
    reviewer_notes TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (player_a_id, player_b_id),
    CHECK (player_a_id < player_b_id)
);

CREATE TABLE IF NOT EXISTS import_checkpoints (
    source TEXT PRIMARY KEY,
    checkpoint TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_players_name ON players(name);
CREATE INDEX IF NOT EXISTS idx_players_club ON players(club_id);
CREATE INDEX IF NOT EXISTS idx_players_position ON players(game_position);
CREATE INDEX IF NOT EXISTS idx_player_sources_player ON player_sources(player_id);
CREATE INDEX IF NOT EXISTS idx_player_sources_source ON player_sources(source);
CREATE INDEX IF NOT EXISTS idx_match_candidates_status
    ON player_match_candidates(status);
"""


@contextmanager
def connect(
    database_path: str | Path = DATABASE_PATH,
) -> Iterator[sqlite3.Connection]:
    """Abre e fecha uma conexão, confirmando transações bem-sucedidas."""
    path = Path(database_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def initialize_database(database_path: str | Path = DATABASE_PATH) -> None:
    """Cria o schema sem apagar dados existentes."""
    with connect(database_path) as connection:
        connection.executescript(SCHEMA)
        source_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(player_sources)")
        }
        source_migrations = {
            "source_club_name": "TEXT",
            "source_position": "TEXT",
            "source_season": "INTEGER",
        }
        for column, definition in source_migrations.items():
            if column not in source_columns:
                connection.execute(
                    f"ALTER TABLE player_sources ADD COLUMN {column} {definition}"
                )
        player_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(players)")
        }
        player_migrations = {
            "overall_edition": "TEXT",
            "season_last_active": "INTEGER",
            "activity_status": (
                "TEXT NOT NULL DEFAULT 'unknown' "
                "CHECK (activity_status IN ('active', 'inactive', 'unknown'))"
            ),
            "phase2_candidate": (
                "INTEGER NOT NULL DEFAULT 1 CHECK (phase2_candidate IN (0, 1))"
            ),
            "needs_position_review": (
                "INTEGER NOT NULL DEFAULT 0 " "CHECK (needs_position_review IN (0, 1))"
            ),
        }
        for column, definition in player_migrations.items():
            if column not in player_columns:
                connection.execute(
                    f"ALTER TABLE players ADD COLUMN {column} {definition}"
                )
        for version in range(1, 6):
            connection.execute(
                "INSERT OR IGNORE INTO schema_version (version) VALUES (?)",
                (version,),
            )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_players_pool "
            "ON players(activity_status, phase2_candidate, overall)"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database",
        type=Path,
        default=DATABASE_PATH,
        help=f"caminho do banco SQLite (padrão: {DATABASE_PATH})",
    )
    args = parser.parse_args()
    initialize_database(args.database)
    print(f"Schema inicializado em {args.database}")


if __name__ == "__main__":
    main()
