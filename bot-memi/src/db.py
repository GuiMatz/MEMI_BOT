"""Schema e conexão SQLite do Bot Memi."""

from __future__ import annotations

import argparse
import re
import sqlite3
import unicodedata
from contextlib import contextmanager
from pathlib import Path
from collections.abc import Iterator

from src.ratings import register_sql_functions

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
    overall INTEGER CHECK (overall IS NULL OR overall BETWEEN 1 AND 100),
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

CREATE TABLE IF NOT EXISTS game_users (
    discord_user_id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    memilets INTEGER NOT NULL DEFAULT 0 CHECK (memilets >= 0),
    last_claim_at TEXT,
    last_daily_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS player_ownership (
    player_id INTEGER PRIMARY KEY REFERENCES players(id) ON DELETE CASCADE,
    discord_user_id TEXT NOT NULL REFERENCES game_users(discord_user_id)
        ON DELETE CASCADE,
    acquired_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS player_balls (
    player_id INTEGER PRIMARY KEY REFERENCES players(id) ON DELETE CASCADE,
    balls INTEGER NOT NULL CHECK (balls BETWEEN 1 AND 10),
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS roll_cycles (
    discord_user_id TEXT NOT NULL REFERENCES game_users(discord_user_id)
        ON DELETE CASCADE,
    cycle_started_at TEXT NOT NULL,
    rolls_used INTEGER NOT NULL CHECK (rolls_used > 0),
    PRIMARY KEY (discord_user_id, cycle_started_at)
);

CREATE TABLE IF NOT EXISTS claim_offers (
    id INTEGER PRIMARY KEY,
    player_id INTEGER NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    roller_id TEXT NOT NULL REFERENCES game_users(discord_user_id)
        ON DELETE CASCADE,
    offer_type TEXT NOT NULL
        CHECK (offer_type IN ('player_claim', 'duplicate_reward')),
    reward_amount INTEGER CHECK (reward_amount IS NULL OR reward_amount > 0),
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'claimed', 'expired', 'unavailable')),
    claimed_by TEXT REFERENCES game_users(discord_user_id) ON DELETE SET NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    claimed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_players_name ON players(name);
CREATE INDEX IF NOT EXISTS idx_players_position ON players(game_position);
CREATE INDEX IF NOT EXISTS idx_player_sources_player ON player_sources(player_id);
CREATE INDEX IF NOT EXISTS idx_player_sources_source ON player_sources(source);
CREATE INDEX IF NOT EXISTS idx_match_candidates_status
    ON player_match_candidates(status);
CREATE INDEX IF NOT EXISTS idx_player_ownership_owner
    ON player_ownership(discord_user_id, acquired_at, player_id);
CREATE INDEX IF NOT EXISTS idx_claim_offers_status
    ON claim_offers(status, expires_at);

CREATE TABLE IF NOT EXISTS user_upgrades (
    discord_user_id TEXT NOT NULL REFERENCES game_users(discord_user_id)
        ON DELETE CASCADE,
    upgrade_key TEXT NOT NULL,
    level INTEGER NOT NULL DEFAULT 0 CHECK (level >= 0),
    roll_tokens INTEGER NOT NULL DEFAULT 0 CHECK (roll_tokens >= 0),
    crate_pity INTEGER NOT NULL DEFAULT 0 CHECK (crate_pity BETWEEN 0 AND 99),
    PRIMARY KEY (discord_user_id, upgrade_key)
);

CREATE TABLE IF NOT EXISTS user_wishes (
    discord_user_id TEXT NOT NULL REFERENCES game_users(discord_user_id)
        ON DELETE CASCADE,
    player_id INTEGER NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    wish_type TEXT NOT NULL CHECK (wish_type IN ('normal', 'star')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (discord_user_id, player_id)
);

CREATE INDEX IF NOT EXISTS idx_user_wishes_player
    ON user_wishes(player_id, wish_type);

CREATE TABLE IF NOT EXISTS game_teams (
    discord_user_id TEXT PRIMARY KEY REFERENCES game_users(discord_user_id)
        ON DELETE CASCADE,
    team_name TEXT NOT NULL DEFAULT '',
    color TEXT NOT NULL DEFAULT '#2E8B57',
    mascot TEXT,
    coach_name TEXT,
    stadium_name TEXT,
    logo_path TEXT,
    kit_path TEXT,
    favorite_league TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS team_tactics (
    discord_user_id TEXT PRIMARY KEY REFERENCES game_teams(discord_user_id)
        ON DELETE CASCADE,
    formation TEXT NOT NULL DEFAULT '4-3-3',
    style TEXT NOT NULL DEFAULT 'balanced'
        CHECK (style IN ('balanced', 'attack', 'counter')),
    captain_player_id INTEGER REFERENCES players(id) ON DELETE SET NULL,
    free_kick_player_id INTEGER REFERENCES players(id) ON DELETE SET NULL,
    penalty_player_id INTEGER REFERENCES players(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS team_lineups (
    discord_user_id TEXT NOT NULL REFERENCES game_teams(discord_user_id)
        ON DELETE CASCADE,
    slot_order INTEGER NOT NULL CHECK (slot_order BETWEEN 1 AND 11),
    player_id INTEGER NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    position TEXT NOT NULL CHECK (
        position IN ('GOL', 'ZAG', 'LD', 'LE', 'MC', 'MEI', 'PE', 'PD', 'CA')
    ),
    PRIMARY KEY (discord_user_id, slot_order),
    UNIQUE (discord_user_id, player_id)
);

CREATE TABLE IF NOT EXISTS trade_offers (
    id INTEGER PRIMARY KEY,
    proposer_id TEXT NOT NULL REFERENCES game_users(discord_user_id)
        ON DELETE CASCADE,
    recipient_id TEXT NOT NULL REFERENCES game_users(discord_user_id)
        ON DELETE CASCADE,
    proposer_player_id INTEGER NOT NULL REFERENCES players(id),
    recipient_player_id INTEGER NOT NULL REFERENCES players(id),
    proposer_memilets INTEGER NOT NULL DEFAULT 0 CHECK (proposer_memilets >= 0),
    recipient_memilets INTEGER NOT NULL DEFAULT 0 CHECK (recipient_memilets >= 0),
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'accepted', 'declined', 'expired')),
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    proposer_confirmed INTEGER NOT NULL DEFAULT 0 CHECK (proposer_confirmed IN (0, 1)),
    recipient_confirmed INTEGER NOT NULL DEFAULT 0 CHECK (recipient_confirmed IN (0, 1)),
    CHECK (proposer_id != recipient_id)
);

CREATE INDEX IF NOT EXISTS idx_trade_offers_pending
    ON trade_offers(recipient_id, status, expires_at);

CREATE TABLE IF NOT EXISTS championships (
    id INTEGER PRIMARY KEY,
    guild_id TEXT NOT NULL,
    competition_date TEXT NOT NULL,
    timezone TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'registration'
        CHECK (status IN ('registration', 'playing', 'completed')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TEXT,
    UNIQUE (guild_id, competition_date)
);

CREATE TABLE IF NOT EXISTS championship_entries (
    championship_id INTEGER NOT NULL REFERENCES championships(id)
        ON DELETE CASCADE,
    discord_user_id TEXT NOT NULL REFERENCES game_users(discord_user_id)
        ON DELETE CASCADE,
    team_name TEXT NOT NULL,
    PRIMARY KEY (championship_id, discord_user_id)
);

CREATE TABLE IF NOT EXISTS championship_fixtures (
    id INTEGER PRIMARY KEY,
    championship_id INTEGER NOT NULL REFERENCES championships(id)
        ON DELETE CASCADE,
    round_no INTEGER NOT NULL,
    match_order INTEGER NOT NULL,
    home_user_id TEXT NOT NULL REFERENCES game_users(discord_user_id),
    away_user_id TEXT NOT NULL REFERENCES game_users(discord_user_id),
    is_final INTEGER NOT NULL DEFAULT 0 CHECK (is_final IN (0, 1)),
    home_goals INTEGER,
    away_goals INTEGER,
    events_json TEXT,
    winner_user_id TEXT REFERENCES game_users(discord_user_id),
    played_at TEXT,
    UNIQUE (championship_id, match_order),
    CHECK (home_user_id != away_user_id)
);

CREATE INDEX IF NOT EXISTS idx_championship_fixtures_pending
    ON championship_fixtures(championship_id, is_final, match_order);

CREATE TABLE IF NOT EXISTS mascots (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    owner TEXT,
    country TEXT,
    image_path TEXT,
    UNIQUE (name, owner)
);

CREATE TABLE IF NOT EXISTS user_bonuses (
    discord_user_id TEXT NOT NULL REFERENCES game_users(discord_user_id)
        ON DELETE CASCADE,
    bonus_key TEXT NOT NULL,
    amount INTEGER NOT NULL DEFAULT 0 CHECK (amount >= 0),
    PRIMARY KEY (discord_user_id, bonus_key)
);

CREATE TABLE IF NOT EXISTS cycle_bonus_rolls (
    discord_user_id TEXT NOT NULL REFERENCES game_users(discord_user_id)
        ON DELETE CASCADE,
    cycle_started_at TEXT NOT NULL,
    amount INTEGER NOT NULL CHECK (amount >= 0),
    PRIMARY KEY (discord_user_id, cycle_started_at)
);

CREATE TABLE IF NOT EXISTS user_cosmetics (
    discord_user_id TEXT NOT NULL REFERENCES game_users(discord_user_id)
        ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK (
        kind IN ('emblema', 'uniforme', 'estadio', 'tecnico', 'liga', 'mascote')
    ),
    item_key TEXT NOT NULL,
    name TEXT NOT NULL,
    image_path TEXT,
    source TEXT NOT NULL DEFAULT 'crate',
    acquired_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (discord_user_id, kind, item_key)
);

CREATE INDEX IF NOT EXISTS idx_user_cosmetics_item ON user_cosmetics(kind, item_key);

CREATE TABLE IF NOT EXISTS tower_floors (
    discord_user_id TEXT NOT NULL REFERENCES game_users(discord_user_id)
        ON DELETE CASCADE,
    floor INTEGER NOT NULL CHECK (floor BETWEEN 1 AND 12),
    times INTEGER NOT NULL DEFAULT 0 CHECK (times >= 0),
    PRIMARY KEY (discord_user_id, floor)
);

CREATE TABLE IF NOT EXISTS tower_ball_grants (
    id INTEGER PRIMARY KEY,
    discord_user_id TEXT NOT NULL REFERENCES game_users(discord_user_id)
        ON DELETE CASCADE,
    player_id INTEGER NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    granted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS mission_progress (
    discord_user_id TEXT NOT NULL REFERENCES game_users(discord_user_id)
        ON DELETE CASCADE,
    period TEXT NOT NULL,
    mission_key TEXT NOT NULL,
    progress INTEGER NOT NULL DEFAULT 0 CHECK (progress >= 0),
    claimed INTEGER NOT NULL DEFAULT 0 CHECK (claimed IN (0, 1)),
    PRIMARY KEY (discord_user_id, period, mission_key)
);

CREATE TABLE IF NOT EXISTS championship_settings (
    guild_id TEXT PRIMARY KEY,
    channel_id TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 0 CHECK (enabled IN (0, 1)),
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


_IDENTIFIER = re.compile(r"[a-z_][a-z0-9_]*")


def _identifier(name: str) -> str:
    """Nomes de coluna montados nas migrações vêm de listas fixas; isto garante que
    nada além de um identificador simples entre numa f-string de SQL."""
    if not _IDENTIFIER.fullmatch(name):
        raise ValueError(f"Identificador SQL inválido: {name!r}")
    return name


@contextmanager
def connect(
    database_path: str | Path = DATABASE_PATH,
) -> Iterator[sqlite3.Connection]:
    """Abre e fecha uma conexão, confirmando transações bem-sucedidas."""
    path = Path(database_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.create_function("normalize_player_name", 1, normalize_player_name)
    register_sql_functions(connection)
    connection.execute("PRAGMA busy_timeout = 30000")
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
            row["name"] for row in connection.execute("PRAGMA table_info(player_sources)")
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
                    f"ALTER TABLE player_sources ADD COLUMN {_identifier(column)} {definition}"
                )
        player_columns = {row["name"] for row in connection.execute("PRAGMA table_info(players)")}
        player_migrations = {
            "overall_edition": "TEXT",
            "season_last_active": "INTEGER",
            "activity_status": (
                "TEXT NOT NULL DEFAULT 'unknown' "
                "CHECK (activity_status IN ('active', 'inactive', 'unknown'))"
            ),
            "phase2_candidate": ("INTEGER NOT NULL DEFAULT 1 CHECK (phase2_candidate IN (0, 1))"),
            "needs_position_review": (
                "INTEGER NOT NULL DEFAULT 0 " "CHECK (needs_position_review IN (0, 1))"
            ),
        }
        for column, definition in player_migrations.items():
            if column not in player_columns:
                connection.execute(
                    f"ALTER TABLE players ADD COLUMN {_identifier(column)} {definition}"
                )
        game_user_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(game_users)")
        }
        if "last_claim_at" not in game_user_columns:
            connection.execute("ALTER TABLE game_users ADD COLUMN last_claim_at TEXT")
        if "last_daily_at" not in game_user_columns:
            connection.execute("ALTER TABLE game_users ADD COLUMN last_daily_at TEXT")
        game_user_migrations = {
            "last_income_at": "TEXT",
            "income_total": "INTEGER NOT NULL DEFAULT 0",
            "tower_spent": "INTEGER NOT NULL DEFAULT 0",
            "box_office": "INTEGER NOT NULL DEFAULT 0",
            "box_office_total": "INTEGER NOT NULL DEFAULT 0",
            "last_rt_at": "TEXT",
        }
        for column, definition in game_user_migrations.items():
            if column not in game_user_columns:
                connection.execute(
                    f"ALTER TABLE game_users ADD COLUMN {_identifier(column)} {definition}"
                )
        trade_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(trade_offers)")
        }
        for column in ("proposer_confirmed", "recipient_confirmed"):
            if column not in trade_columns:
                connection.execute(
                    f"ALTER TABLE trade_offers ADD COLUMN {_identifier(column)} INTEGER NOT NULL DEFAULT 0 "
                    "CHECK (" + column + " IN (0, 1))"
                )

        _migrate_roll_cycle_limit(connection)
        team_columns = {row["name"] for row in connection.execute("PRAGMA table_info(game_teams)")}
        for column in ("coach_name", "stadium_name", "kit_path", "favorite_league"):
            if column not in team_columns:
                connection.execute(f"ALTER TABLE game_teams ADD COLUMN {_identifier(column)} TEXT")
        _migrate_upgrade_keys(connection)
        fixture_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(championship_fixtures)")
        }
        if "winner_user_id" not in fixture_columns:
            connection.execute(
                "ALTER TABLE championship_fixtures ADD COLUMN winner_user_id TEXT "
                "REFERENCES game_users(discord_user_id)"
            )

        player_columns = {row["name"] for row in connection.execute("PRAGMA table_info(players)")}
        if "club_id" in player_columns:
            _migrate_players_without_club_foreign_key(connection, player_columns)
        _migrate_player_overall_100(connection)

        upgrades_v2_done = connection.execute(
            "SELECT 1 FROM schema_version WHERE version = 13"
        ).fetchone()
        if not upgrades_v2_done:
            _migrate_upgrades_v2(connection)
        for version in range(1, 14):
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


def _migrate_upgrade_keys(connection: sqlite3.Connection) -> None:
    """Tira a lista fixa de chaves de user_upgrades: as chaves válidas ficam no código."""
    row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'user_upgrades'"
    ).fetchone()
    if row is None or "upgrade_key IN" not in re.sub(r"\s+", " ", row["sql"] or ""):
        return
    connection.execute("ALTER TABLE user_upgrades RENAME TO user_upgrades_v12")
    connection.execute("""CREATE TABLE user_upgrades (
            discord_user_id TEXT NOT NULL REFERENCES game_users(discord_user_id)
                ON DELETE CASCADE,
            upgrade_key TEXT NOT NULL,
            level INTEGER NOT NULL DEFAULT 0 CHECK (level >= 0),
            roll_tokens INTEGER NOT NULL DEFAULT 0 CHECK (roll_tokens >= 0),
            crate_pity INTEGER NOT NULL DEFAULT 0 CHECK (crate_pity BETWEEN 0 AND 99),
            PRIMARY KEY (discord_user_id, upgrade_key)
        )""")
    connection.execute("""INSERT INTO user_upgrades
           SELECT discord_user_id, upgrade_key, level, roll_tokens, crate_pity
           FROM user_upgrades_v12""")
    connection.execute("DROP TABLE user_upgrades_v12")


# Loja V2: limites novos e o que cada nível antigo custava (para devolver o excedente).
_V2_LIMITS = {"rolls": 10, "wishes": 10, "exclusivity": 10, "daily_max": 5, "star_wishes": 5}
_V1_LEVEL_COST = {
    "rolls": lambda level: 5_000 * level,
    "wishes": lambda level: 3_000 * level,
    "daily": lambda level: 5_000,
    "exclusivity": lambda level: 5_000 * level,
    "duplicate_bonus": lambda level: 4_000 * level,
    "star_wishes": lambda level: 0,
}


def _migrate_upgrades_v2(connection: sqlite3.Connection) -> None:
    """Converte os upgrades da loja antiga para a V2 sem ninguém perder Memilets.

    - `daily` antigo (+100 no máximo do daily) vira `daily_max`; `daily` agora é -10 min;
    - o bônus de duplicata deixou de existir e é devolvido;
    - níveis acima dos limites novos são devolvidos pelo preço antigo;
    - Star Wishes acima do limite da loja viram bônus permanente (vinham de crates).
    """
    rows = [
        dict(row)
        for row in connection.execute(
            "SELECT discord_user_id, upgrade_key, level FROM user_upgrades WHERE level > 0"
        )
    ]
    for row in rows:
        user_id, key, level = row["discord_user_id"], row["upgrade_key"], int(row["level"])
        if key not in _V1_LEVEL_COST:
            continue
        new_key = {"daily": "daily_max", "duplicate_bonus": None}.get(key, key)
        kept = min(level, _V2_LIMITS.get(new_key, 0)) if new_key else 0
        refund = sum(_V1_LEVEL_COST[key](number) for number in range(kept + 1, level + 1))
        connection.execute(
            "UPDATE user_upgrades SET level = 0 WHERE discord_user_id = ? AND upgrade_key = ?",
            (user_id, key),
        )
        if new_key and kept:
            connection.execute(
                """
                INSERT INTO user_upgrades (discord_user_id, upgrade_key, level) VALUES (?, ?, ?)
                ON CONFLICT(discord_user_id, upgrade_key) DO UPDATE SET level = excluded.level
                """,
                (user_id, new_key, kept),
            )
        if key == "star_wishes" and level > kept:
            connection.execute(
                """
                INSERT INTO user_bonuses (discord_user_id, bonus_key, amount)
                VALUES (?, 'star_wishes', ?)
                ON CONFLICT(discord_user_id, bonus_key)
                DO UPDATE SET amount = user_bonuses.amount + excluded.amount
                """,
                (user_id, level - kept),
            )
        if refund:
            connection.execute(
                "UPDATE game_users SET memilets = memilets + ? WHERE discord_user_id = ?",
                (refund, user_id),
            )
    connection.execute("""
        DELETE FROM user_upgrades
        WHERE level = 0 AND roll_tokens = 0 AND crate_pity = 0 AND upgrade_key != 'rolls'
        """)


def _migrate_player_overall_100(connection: sqlite3.Connection) -> None:
    """Recria players para permitir a carta especial de overall 100."""
    row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'players'"
    ).fetchone()
    if row is None or "BETWEEN 1 AND 99" not in (row["sql"] or ""):
        return
    connection.execute("ALTER TABLE players RENAME TO players_v10")
    connection.execute("""
        CREATE TABLE players (
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
            overall INTEGER CHECK (overall IS NULL OR overall BETWEEN 1 AND 100),
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
    columns = [row["name"] for row in connection.execute("PRAGMA table_info(players_v10)")]
    transferable = [
        column
        for column in (
            "id",
            "name",
            "display_name",
            "tm_player_id",
            "birth_date",
            "nationality",
            "club_name",
            "original_position",
            "game_position",
            "overall",
            "overall_source",
            "overall_edition",
            "market_value_eur",
            "season_last_active",
            "activity_status",
            "phase2_candidate",
            "needs_position_review",
            "photo_url",
            "photo_path",
            "created_at",
            "updated_at",
        )
        if column in columns
    ]
    names = ", ".join(transferable)
    connection.execute(f"INSERT INTO players ({names}) SELECT {names} FROM players_v10")
    connection.execute("DROP TABLE players_v10")


def _migrate_roll_cycle_limit(connection: sqlite3.Connection) -> None:
    """Allow purchased rolls while preserving usage in existing cycles."""
    row = connection.execute("""
        SELECT sql FROM sqlite_master
        WHERE type = 'table' AND name = 'roll_cycles'
        """).fetchone()
    if row is None or "BETWEEN 1 AND 10" not in row["sql"]:
        return
    connection.execute("ALTER TABLE roll_cycles RENAME TO roll_cycles_v8")
    connection.execute("""
        CREATE TABLE roll_cycles (
            discord_user_id TEXT NOT NULL REFERENCES game_users(discord_user_id)
                ON DELETE CASCADE,
            cycle_started_at TEXT NOT NULL,
            rolls_used INTEGER NOT NULL CHECK (rolls_used > 0),
            PRIMARY KEY (discord_user_id, cycle_started_at)
        )
        """)
    connection.execute("""
        INSERT INTO roll_cycles (discord_user_id, cycle_started_at, rolls_used)
        SELECT discord_user_id, cycle_started_at, rolls_used FROM roll_cycles_v8
        """)
    connection.execute("DROP TABLE roll_cycles_v8")


def _migrate_players_without_club_foreign_key(
    connection: sqlite3.Connection, old_columns: set[str]
) -> None:
    """Recria players com clube textual e IDs de origem, preservando dependências."""
    if "players_v6" in {
        row["name"]
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
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
            overall INTEGER CHECK (overall IS NULL OR overall BETWEEN 1 AND 100),
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
    """Mantém o nome exibido limpo; detalhes ficam disponíveis nos resultados."""
    players = [dict(row) for row in connection.execute("SELECT id, name FROM players ORDER BY id")]

    connection.executemany(
        "UPDATE players SET display_name = ? WHERE id = ?",
        [(str(player["name"]), player["id"]) for player in players],
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
