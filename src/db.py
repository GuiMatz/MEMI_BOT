"""Schema e conexão SQLite do Bot Memi."""

from __future__ import annotations

import argparse
import re
import sqlite3
import unicodedata
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
    display_name TEXT NOT NULL DEFAULT '',
    tm_player_id TEXT UNIQUE,
    birth_date TEXT,
    nationality TEXT,
    club_name TEXT,
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
    source_league_name TEXT,
    source_snapshot_date TEXT,
    UNIQUE (source, source_id)
);

CREATE TABLE IF NOT EXISTS ea_import_stats (
    source TEXT PRIMARY KEY,
    matched INTEGER NOT NULL DEFAULT 0,
    ambiguous INTEGER NOT NULL DEFAULT 0,
    new_players INTEGER NOT NULL DEFAULT 0,
    brazil_excluded INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
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
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.executescript(SCHEMA)
        source_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(player_sources)")
        }
        source_migrations = {
            "source_club_name": "TEXT",
            "source_position": "TEXT",
            "source_season": "INTEGER",
            "source_league_name": "TEXT",
            "source_snapshot_date": "TEXT",
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

        player_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(players)")
        }
        if "club_id" in player_columns:
            _migrate_players_without_club_foreign_key(connection, player_columns)

        for version in range(1, 7):
            connection.execute(
                "INSERT OR IGNORE INTO schema_version (version) VALUES (?)",
                (version,),
            )
        connection.execute("""
            CREATE INDEX IF NOT EXISTS idx_players_pool
            ON players(activity_status, phase2_candidate, game_position, overall)
            """)
        connection.commit()
        connection.execute("PRAGMA foreign_keys = ON")
        violations = connection.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise sqlite3.IntegrityError(
                f"Violação de chave estrangeira após migração do schema: "
                f"{len(violations)} registro(s)."
            )


def _migrate_players_without_club_foreign_key(
    connection: sqlite3.Connection, old_columns: set[str]
) -> None:
    """Recria players com clube textual e IDs de origem, preservando dependências."""
    if "players_v6" in {
        row["name"]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }:
        raise sqlite3.OperationalError(
            "A tabela temporária players_v6 já existe; verifique a migração interrompida."
        )

    display_name = (
        "COALESCE(NULLIF(p.display_name, ''), p.name)"
        if "display_name" in old_columns
        else "p.name"
    )
    transfermarkt_id = (
        "p.tm_player_id"
        if "tm_player_id" in old_columns
        else """
        (SELECT ps.source_id FROM player_sources AS ps
         WHERE ps.player_id = p.id AND ps.source = 'transfermarkt'
         ORDER BY ps.id LIMIT 1)
        """
    )
    club_name = (
        "p.club_name"
        if "club_name" in old_columns
        else "(SELECT c.name FROM clubs AS c WHERE c.id = p.club_id)"
    )
    connection.execute("""
        CREATE TABLE players_v6 (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            display_name TEXT NOT NULL DEFAULT '',
            tm_player_id TEXT UNIQUE,
            birth_date TEXT,
            nationality TEXT,
            club_name TEXT,
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
        )
        """)
    connection.execute(f"""
        INSERT INTO players_v6 (
            id, name, display_name, tm_player_id, birth_date, nationality,
            club_name, original_position, game_position, overall, overall_source,
            overall_edition, market_value_eur, season_last_active,
            activity_status, phase2_candidate, needs_position_review, photo_url,
            photo_path, created_at, updated_at
        )
        SELECT
            p.id, p.name, {display_name}, {transfermarkt_id}, p.birth_date,
            p.nationality, {club_name}, p.original_position, p.game_position,
            p.overall, p.overall_source, p.overall_edition, p.market_value_eur,
            p.season_last_active, p.activity_status, p.phase2_candidate,
            p.needs_position_review, p.photo_url, p.photo_path, p.created_at,
            p.updated_at
        FROM players AS p
        """)
    connection.execute("DROP TABLE players")
    connection.execute("ALTER TABLE players_v6 RENAME TO players")
    connection.execute("CREATE INDEX idx_players_name ON players(name)")
    connection.execute("CREATE INDEX idx_players_position ON players(game_position)")


def normalize_player_name(name: str | None) -> str:
    """Normaliza acentos e pontuação para comparar nomes e exibições."""
    if not name:
        return ""
    normalized = unicodedata.normalize("NFKD", name)
    normalized = "".join(
        character for character in normalized if not unicodedata.combining(character)
    )
    return re.sub(r"[^a-zA-Z0-9]+", " ", normalized).casefold().strip()


def refresh_display_names(connection: sqlite3.Connection) -> None:
    """Diferencia homônimos com clube, ano de nascimento e ID se necessário."""
    players = [
        dict(row)
        for row in connection.execute(
            "SELECT id, name, club_name, birth_date FROM players ORDER BY id"
        )
    ]
    by_name: dict[str, list[dict[str, object]]] = {}
    for player in players:
        normalized = normalize_player_name(str(player["name"]))
        by_name.setdefault(normalized, []).append(player)

    for group in by_name.values():
        if len(group) == 1:
            group[0]["display_name"] = group[0]["name"]
            continue
        labels: dict[str, int] = {}
        for player in group:
            club = str(player["club_name"] or "").strip()
            birth_date = str(player["birth_date"] or "")
            birth_year = birth_date[:4] if re.match(r"^\d{4}", birth_date) else ""
            details = " · ".join(value for value in (club, birth_year) if value)
            label = f"{player['name']} ({details})" if details else str(player["name"])
            labels[label] = labels.get(label, 0) + 1
            player["display_name"] = label
        for player in group:
            label = str(player["display_name"])
            if labels[label] > 1:
                player["display_name"] = f"{label} [ID {player['id']}]"

    connection.executemany(
        "UPDATE players SET display_name = ? WHERE id = ?",
        [(player["display_name"], player["id"]) for player in players],
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
