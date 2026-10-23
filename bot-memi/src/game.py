"""Consultas e operações básicas da conta de jogo."""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any

from src.db import DATABASE_PATH, GAME_POSITIONS, ROOT, connect, normalize_player_name
from src.ratings import EFFECTIVE_OVERALL_SQL, GAME_OVERALL_SQL, POOL_SQL, RARITY_TIERS

CATALOG_ASSET_DIRECTORIES = {
    "uniformes": (
        "teams/camisas",
        "teams/camisas2",
        "teams/camisas3",
    ),
    "logos": ("teams/escudos",),
}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg", ".avif", ".jfif", ".bmp"}


def get_collection_left(database_path=DATABASE_PATH) -> dict[str, Any]:
    """Conta o que ainda está livre, no total e por série de raridade."""
    with connect(database_path) as connection:
        rows = connection.execute(f"""
            SELECT rarity_index({GAME_OVERALL_SQL}) AS tier,
                   COALESCE(p.overall_source, '') = 'Cartola' AS cartola,
                   COUNT(*) AS total,
                   SUM(o.player_id IS NULL) AS remaining
            FROM players AS p
            LEFT JOIN player_ownership AS o ON o.player_id = p.id
            WHERE {POOL_SQL}
            GROUP BY tier, cartola
            """).fetchall()
        used_paths = {
            str(path)
            for row in connection.execute("SELECT logo_path, kit_path FROM game_teams")
            for path in (row["logo_path"], row["kit_path"])
            if path
        }

    tiers = {index: [0, 0] for index in range(len(RARITY_TIERS))}
    cartola = [0, 0]
    for row in rows:
        bucket = cartola if row["cartola"] else tiers[int(row["tier"])]
        bucket[0] += int(row["remaining"] or 0)
        bucket[1] += int(row["total"])
    counts: dict[str, Any] = {
        "jogadores": sum(free for free, _ in tiers.values()) + cartola[0],
        "jogadores_total": sum(total for _, total in tiers.values()) + cartola[1],
        "series": [
            (f"{RARITY_TIERS[index][1]} {RARITY_TIERS[index][0]}", free, total)
            for index, (free, total) in tiers.items()
            if total
        ],
        "cartola": tuple(cartola),
    }
    asset_root = ROOT / "data" / "assets" / "brasfoot"
    for label, directories in CATALOG_ASSET_DIRECTORIES.items():
        files = [
            path.relative_to(ROOT).as_posix()
            for directory in directories
            for path in (asset_root / directory).rglob("*")
            if path.is_file() and path.suffix.casefold() in IMAGE_SUFFIXES
        ]
        counts[label] = sum(1 for path in files if path not in used_paths)
        counts[f"{label}_total"] = len(files)
    return counts


def register_user(
    discord_user_id: int | str,
    display_name: str,
    database_path=DATABASE_PATH,
) -> dict[str, Any]:
    """Cria a conta na primeira utilização e mantém o nome atual do Discord."""
    user_id = str(discord_user_id)
    with connect(database_path) as connection:
        connection.execute(
            """
            INSERT INTO game_users (discord_user_id, display_name)
            VALUES (?, ?)
            ON CONFLICT(discord_user_id) DO UPDATE SET
                display_name = excluded.display_name,
                updated_at = CURRENT_TIMESTAMP
            """,
            (user_id, display_name),
        )
        row = connection.execute(
            """
            SELECT discord_user_id, display_name, memilets, created_at
            FROM game_users
            WHERE discord_user_id = ?
            """,
            (user_id,),
        ).fetchone()
        return dict(row)


def get_wallet(
    discord_user_id: int | str,
    database_path=DATABASE_PATH,
) -> dict[str, Any]:
    """Retorna o saldo persistido de uma conta de jogo."""
    with connect(database_path) as connection:
        row = connection.execute(
            """
            SELECT discord_user_id, display_name, memilets
            FROM game_users
            WHERE discord_user_id = ?
            """,
            (str(discord_user_id),),
        ).fetchone()
    if row is None:
        raise LookupError("A conta de jogo ainda não foi cadastrada.")
    return dict(row)


def list_owned_players(
    discord_user_id: int | str,
    database_path=DATABASE_PATH,
    limit: int = 20,
    *,
    position: str | None = None,
    min_overall: int | None = None,
    max_overall: int | None = None,
    club_query: str | None = None,
    league_query: str | None = None,
    sort_by: str = "acquired",
) -> list[dict[str, Any]]:
    """Lista os jogadores do elenco de uma pessoa por ordem de aquisição."""
    if limit < 1:
        raise ValueError("limit deve ser maior que zero.")
    if position is not None and position not in GAME_POSITIONS:
        raise ValueError("position inválida.")
    if min_overall is not None and not 1 <= min_overall <= 100:
        raise ValueError("min_overall deve ficar entre 1 e 100.")
    if max_overall is not None and not 1 <= max_overall <= 100:
        raise ValueError("max_overall deve ficar entre 1 e 100.")
    if min_overall is not None and max_overall is not None and min_overall > max_overall:
        raise ValueError("min_overall não pode ser maior que max_overall.")
    order_by = {
        "acquired": "o.acquired_at, o.player_id",
        "overall": "overall DESC, name, p.id",
        "name": "name COLLATE NOCASE, p.id",
    }.get(sort_by)
    if order_by is None:
        raise ValueError("sort_by inválido.")
    clauses = ["o.discord_user_id = ?"]
    parameters: list[Any] = [str(discord_user_id)]
    if position is not None:
        clauses.append("p.game_position = ?")
        parameters.append(position)
    effective_overall = EFFECTIVE_OVERALL_SQL
    if min_overall is not None:
        clauses.append(f"{effective_overall} >= ?")
        parameters.append(min_overall)
    if max_overall is not None:
        clauses.append(f"{effective_overall} <= ?")
        parameters.append(max_overall)
    if club_query:
        clauses.append("normalize_player_name(p.club_name) LIKE ?")
        parameters.append(f"%{normalize_player_name(club_query)}%")
    if league_query:
        clauses.append("""
            EXISTS (
                SELECT 1 FROM player_sources AS ps
                WHERE ps.player_id = p.id
                  AND normalize_player_name(ps.source_league_name) LIKE ?
            )
            """)
        parameters.append(f"%{normalize_player_name(league_query)}%")
    parameters.append(limit)
    with connect(database_path) as connection:
        rows = connection.execute(
            f"""
            SELECT p.id, COALESCE(NULLIF(p.display_name, ''), p.name) AS name,
                   p.club_name, p.game_position, p.photo_path, p.overall_source,
                   {effective_overall} AS overall,
                   COALESCE(b.balls, 0) AS balls, o.acquired_at,
                   o.locked, o.pack_origin, o.pack_locked_until
            FROM player_ownership AS o
            JOIN players AS p ON p.id = o.player_id
            LEFT JOIN player_balls AS b ON b.player_id = p.id
            WHERE {' AND '.join(clauses)}
            ORDER BY {order_by}
            LIMIT ?
            """,
            parameters,
        ).fetchall()
    return [dict(row) for row in rows]


def search_players(
    query: str,
    database_path=DATABASE_PATH,
    limit: int = 5,
) -> list[dict[str, Any]]:
    """Busca jogadores pelo nome, sem exigir acentos ou a pontuação exata."""
    normalized_query = normalize_player_name(query)
    if not normalized_query:
        return []
    if limit < 1:
        raise ValueError("limit deve ser maior que zero.")
    columns = f"""
        p.id, p.name, p.display_name, p.club_name, p.nationality,
        p.game_position, {GAME_OVERALL_SQL} AS overall, p.overall_source,
        p.overall_edition, p.photo_path, COALESCE(b.balls, 0) AS balls,
        o.discord_user_id AS owner_id, owner.display_name AS owner_name
    """
    joins = """
        FROM players AS p
        LEFT JOIN player_balls AS b ON b.player_id = p.id
        LEFT JOIN player_ownership AS o ON o.player_id = p.id
        LEFT JOIN game_users AS owner ON owner.discord_user_id = o.discord_user_id
    """
    with connect(database_path) as connection:
        if query.strip().isdigit():
            rows = connection.execute(
                f"SELECT {columns} {joins} WHERE p.id = ?",
                (int(query.strip()),),
            ).fetchall()
            return [dict(row) for row in rows]
        rows = connection.execute(
            f"""
            SELECT {columns} {joins}
            WHERE normalize_player_name(p.name) LIKE ?
               OR normalize_player_name(p.display_name) LIKE ?
            ORDER BY
                CASE WHEN normalize_player_name(p.name) = ? THEN 0 ELSE 1 END,
                overall DESC, p.name, p.id
            LIMIT ?
            """,
            (
                f"%{normalized_query}%",
                f"%{normalized_query}%",
                normalized_query,
                limit,
            ),
        ).fetchall()
    return [dict(row) for row in rows]


def list_players_by_team_or_series(
    query: str,
    database_path=DATABASE_PATH,
    *,
    limit: int = 5000,
) -> list[dict[str, Any]]:
    """Lista jogadores ativos de um clube ou liga, ordenados por overall."""
    normalized_query = normalize_player_name(query)
    if not normalized_query:
        return []
    if limit < 1:
        raise ValueError("limit deve ser maior que zero.")
    pattern = f"%{normalized_query}%"
    with connect(database_path) as connection:
        rows = connection.execute(
            f"""
            SELECT DISTINCT p.id,
                   COALESCE(NULLIF(p.display_name, ''), p.name) AS name,
                   p.club_name, p.game_position, {GAME_OVERALL_SQL} AS overall,
                   COALESCE(ps.source_league_name, '') AS league_name
            FROM players AS p
            LEFT JOIN player_sources AS ps ON ps.player_id = p.id
            WHERE {POOL_SQL}
              AND (
                  normalize_player_name(p.club_name) LIKE ?
                  OR normalize_player_name(ps.source_league_name) LIKE ?
              )
            ORDER BY overall DESC, name COLLATE NOCASE, p.id
            LIMIT ?
            """,
            (pattern, pattern, limit),
        ).fetchall()
    return [dict(row) for row in rows]


def get_top_players(
    database_path=DATABASE_PATH,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Retorna os jogadores ativos e elegíveis com maior overall."""
    if limit < 1:
        raise ValueError("limit deve ser maior que zero.")
    with connect(database_path) as connection:
        rows = connection.execute(
            f"""
            SELECT p.id, COALESCE(NULLIF(p.display_name, ''), p.name) AS name,
                   p.club_name, p.game_position,
                   {EFFECTIVE_OVERALL_SQL} AS overall
            FROM players AS p
            LEFT JOIN player_balls AS b ON b.player_id = p.id
            WHERE {POOL_SQL}
            ORDER BY overall DESC, name, p.id
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


# Palavras que só descrevem o arquivo e não fazem parte do nome exibido.
_ASSET_NOISE = {
    "logo",
    "logos",
    "escudo",
    "escudos",
    "emblema",
    "emblemas",
    "mascote",
    "mascotes",
    "mascot",
    "mascots",
    "cartola",
    "cartolafc",
    "badge",
    "crest",
    "png",
    "jpg",
    "svg",
    "webp",
    "icon",
    "icone",
    "oficial",
    "official",
    "vetor",
    "vector",
    "hd",
    "img",
    "image",
    "imagem",
    "camisa",
    "uniforme",
    "kit",
    "novo",
    "nova",
    "new",
    "final",
    "copy",
    "copia",
}
_LOWERCASE_WORDS = {"de", "da", "do", "das", "dos", "e"}
_NUMERIC_TOKEN = re.compile(r"(?:v?\d+|\d+x\d+|\(\d+\))", re.I)
_WORD_SEPARATOR = re.compile(r"[\s_\-.]+")
_CAMEL_CASE = re.compile(r"(?<=[a-zà-ÿ])(?=[A-Z])")
_STATES = {
    "ac",
    "al",
    "am",
    "ap",
    "ba",
    "ce",
    "df",
    "es",
    "go",
    "ma",
    "mg",
    "ms",
    "mt",
    "pa",
    "pb",
    "pe",
    "pi",
    "pr",
    "rj",
    "rn",
    "ro",
    "rr",
    "rs",
    "sc",
    "se",
    "sp",
    "to",
}
# Nome oficial dos clubes brasileiros, pela grafia compacta (sem espaço nem acento).
KNOWN_TEAMS = {
    "saopaulo": "São Paulo",
    "spfc": "São Paulo",
    "gremio": "Grêmio",
    "atleticomg": "Atlético-MG",
    "atleticomineiro": "Atlético-MG",
    "galo": "Atlético-MG",
    "atleticopr": "Athletico-PR",
    "athleticopr": "Athletico-PR",
    "athleticoparanaense": "Athletico-PR",
    "atleticoparanaense": "Athletico-PR",
    "atleticogo": "Atlético-GO",
    "atleticogoianiense": "Atlético-GO",
    "americamg": "América-MG",
    "americamineiro": "América-MG",
    "vitoria": "Vitória",
    "ceara": "Ceará",
    "criciuma": "Criciúma",
    "cuiaba": "Cuiabá",
    "goias": "Goiás",
    "avai": "Avaí",
    "parana": "Paraná",
    "nautico": "Náutico",
    "bragantino": "Red Bull Bragantino",
    "redbullbragantino": "Red Bull Bragantino",
    "rbbragantino": "Red Bull Bragantino",
    "vasco": "Vasco da Gama",
    "vascodagama": "Vasco da Gama",
    "botafogosp": "Botafogo-SP",
    "operario": "Operário-PR",
    "operariopr": "Operário-PR",
    "ferroviaria": "Ferroviária",
    "pontepreta": "Ponte Preta",
    "voltaredonda": "Volta Redonda",
    "vilanova": "Vila Nova",
    "saobernardo": "São Bernardo",
    "novorizontino": "Novorizontino",
    "crb": "CRB",
    "csa": "CSA",
    "abc": "ABC",
    "sport": "Sport",
    "sportrecife": "Sport",
    "internacional": "Internacional",
    "inter": "Internacional",
    "flamengo": "Flamengo",
    "fluminense": "Fluminense",
    "corinthians": "Corinthians",
    "palmeiras": "Palmeiras",
    "santos": "Santos",
    "cruzeiro": "Cruzeiro",
    "fortaleza": "Fortaleza",
    "bahia": "Bahia",
    "juventude": "Juventude",
    "mirassol": "Mirassol",
    "chapecoense": "Chapecoense",
    "coritiba": "Coritiba",
    "guarani": "Guarani",
    "remo": "Remo",
    "paysandu": "Paysandu",
    "amazonas": "Amazonas",
    "botafogo": "Botafogo",
    "santacruz": "Santa Cruz",
    "brusque": "Brusque",
    "londrina": "Londrina",
    "tombense": "Tombense",
    "ituano": "Ituano",
    "figueirense": "Figueirense",
    "joinville": "Joinville",
    "shakhtar": "Shakhtar Donetsk",
    "shakhtardonetsk": "Shakhtar Donetsk",
    "shaktardonetsk": "Shakhtar Donetsk",
}


def pretty_asset_name(stem: str) -> str:
    """Transforma `flamengo_logo_2024-v2` em `Flamengo` e `SaoPaulo` em `São Paulo`."""
    raw_words = [
        part
        for word in _WORD_SEPARATOR.split(stem.strip())
        for part in _CAMEL_CASE.split(word)
        if part
    ]
    words = (
        [
            word
            for word in raw_words
            if normalize_player_name(word) not in _ASSET_NOISE
            and not _NUMERIC_TOKEN.fullmatch(word)
        ]
        or raw_words
        or [stem]
    )
    # Logos do Cartola vêm como "Time Universo do Cartola": o "Universo do" também sai.
    while (
        len(words) > 2
        and normalize_player_name(words[-2]) == "universo"
        and normalize_player_name(words[-1]) in {"do", "da"}
    ):
        words = words[:-2]
    compact = normalize_player_name("".join(words)).replace(" ", "")
    if compact in KNOWN_TEAMS:
        return KNOWN_TEAMS[compact]
    pretty = []
    for index, word in enumerate(words):
        lowered = word.casefold()
        if index and lowered in _LOWERCASE_WORDS:
            pretty.append(lowered)
        elif index and lowered in _STATES:
            pretty.append(word.upper())
        elif word.isupper() and len(word) <= 3:
            pretty.append(word)
        else:
            pretty.append(word[:1].upper() + word[1:].lower())
    return " ".join(pretty)


ASSET_KINDS = {
    "uniforme": (
        "Uniforme",
        (
            "data/assets/brasfoot/teams/camisas",
            "data/assets/brasfoot/teams/camisas2",
            "data/assets/brasfoot/teams/camisas3",
        ),
    ),
    # Clubes e logos são a mesma coisa no jogo: os dois aparecem como "Emblema".
    "emblema": ("Emblema", ("data/assets/brasfoot/teams/escudos",)),
    "logo_cartola": ("Emblema", ("data/assets/cartola/logos",)),
    "mascote": ("Mascote", ("data/assets/cartola/mascots",)),
}


def asset_display_name(kind: str, stem: str) -> str:
    """Padroniza o nome: logos mostram só o time; mascotes, `Time (Mascote)`."""
    name = pretty_asset_name(stem)
    return f"{name} (Mascote)" if kind == "mascote" else name


def list_assets(kind: str) -> list[dict[str, Any]]:
    """Arquivos locais de uma categoria, com nome padronizado e sem repetição.

    Uniformes das pastas `camisas2` e `camisas3` ganham `(Uniforme 2)` e `(Uniforme 3)`,
    para não sumirem atrás do uniforme principal do mesmo time.
    """
    _, directories = ASSET_KINDS[kind]
    named = sorted(
        (
            (_asset_name(kind, path.stem, index), path)
            for index, directory in enumerate(directories)
            for path in (ROOT / directory).rglob("*")
            if path.is_file() and path.suffix.casefold() in IMAGE_SUFFIXES
        ),
        key=lambda pair: (pair[0].casefold(), str(pair[1])),
    )
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    for name, path in named:
        if name.casefold() in seen:
            continue
        seen.add(name.casefold())
        entries.append({"name": name, "kind": kind, "path": str(path.relative_to(ROOT))})
    return entries


def _asset_name(kind: str, stem: str, directory_index: int) -> str:
    name = asset_display_name(kind, stem)
    if kind == "uniforme" and directory_index:
        return f"{name} (Uniforme {directory_index + 1})"
    return name


def get_catalog_entries(
    category: str,
    database_path=DATABASE_PATH,
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    """Lista uma categoria do catálogo em ordem estável para os embeds."""
    if limit < 1 or offset < 0:
        raise ValueError("limite e deslocamento inválidos.")
    normalized = normalize_player_name(category)
    if normalized in {"jogador", "jogadores", "player", "players"}:
        with connect(database_path) as connection:
            total = connection.execute(
                f"SELECT COUNT(*) AS total FROM players AS p WHERE {POOL_SQL}"
            ).fetchone()["total"]
            rows = connection.execute(
                f"""SELECT p.id, COALESCE(NULLIF(p.display_name, ''), p.name) AS name,
                          {GAME_OVERALL_SQL} AS overall, p.game_position
                   FROM players AS p
                   WHERE {POOL_SQL}
                   ORDER BY overall DESC, name COLLATE NOCASE, p.id
                   LIMIT ? OFFSET ?""",
                (limit, offset),
            ).fetchall()
        return [dict(row) for row in rows], int(total)

    if normalized == "cartola":
        with connect(database_path) as connection:
            players = connection.execute(
                f"""SELECT p.id, COALESCE(NULLIF(p.display_name, ''), p.name) AS name,
                          {GAME_OVERALL_SQL} AS overall, p.game_position
                   FROM players AS p
                   WHERE p.overall_source = 'Cartola'
                   ORDER BY overall DESC, name COLLATE NOCASE, p.id"""
            ).fetchall()
        entries = [dict(row) for row in players]
        entries.extend(list_assets("logo_cartola"))
        entries.extend(list_assets("mascote"))
        return entries[offset : offset + limit], len(entries)

    if normalized in {"mascote", "mascotes"}:
        with connect(database_path) as connection:
            rows = connection.execute(
                """SELECT name || COALESCE(' (' || owner || ')', '') AS name FROM mascots
                   ORDER BY name COLLATE NOCASE"""
            ).fetchall()
        entries = [{"name": row["name"]} for row in rows]
        known = {normalize_player_name(entry["name"]) for entry in entries}
        entries += [
            entry
            for entry in list_assets("mascote")
            if normalize_player_name(entry["name"]) not in known
        ]
        return entries[offset : offset + limit], len(entries)

    if normalized in {"emblema", "emblemas", "logo", "logos", "clube", "clubes", "escudos"}:
        entries = []
        seen: set[str] = set()
        for entry in list_assets("emblema") + list_assets("logo_cartola"):
            key = normalize_player_name(entry["name"])
            if key not in seen:
                seen.add(key)
                entries.append(entry)
        entries.sort(key=lambda entry: entry["name"].casefold())
        return entries[offset : offset + limit], len(entries)
    if normalized in {"uniforme", "uniformes"}:
        entries = list_assets("uniforme")
        return entries[offset : offset + limit], len(entries)

    table_categories = {
        "tecnico": "coaches",
        "tecnicos": "coaches",
        "estadio": "stadiums",
        "estadios": "stadiums",
        "liga": "leagues",
        "ligas": "leagues",
    }
    table = table_categories.get(normalized)
    if table is None:
        return [], 0
    with connect(database_path) as connection:
        rows = connection.execute(
            f"SELECT name FROM {table} ORDER BY name COLLATE NOCASE"
        ).fetchall()
    entries = [{"name": row["name"]} for row in rows]
    return entries[offset : offset + limit], len(entries)


# Tabelas que o mm!scout pesquisa além dos jogadores: (tabela, nome exibido, imagem).
SCOUT_TABLES = {
    "Técnico": ("coaches", "name", "photo_path"),
    "Estádio": ("stadiums", "name", "photo_path"),
    "Emblema": ("clubs", "name", "logo_path"),
    "Liga": ("leagues", "name", "logo_path"),
    "Mascote": ("mascots", "name || COALESCE(' (' || owner || ')', '')", "image_path"),
}


def search_catalog(
    query: str,
    database_path=DATABASE_PATH,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Procura técnicos, estádios, clubes, ligas, uniformes, emblemas, logos e mascotes."""
    normalized_query = normalize_player_name(query)
    if not normalized_query:
        return []
    results: list[dict[str, Any]] = []
    with connect(database_path) as connection:
        for label, (table, name_sql, image_sql) in SCOUT_TABLES.items():
            rows = connection.execute(
                f"""
                SELECT id, {name_sql} AS name, {image_sql} AS image_path FROM {table}
                WHERE normalize_player_name({name_sql}) LIKE ?
                ORDER BY name COLLATE NOCASE
                LIMIT ?
                """,
                (f"%{normalized_query}%", limit),
            ).fetchall()
            results.extend(
                {
                    "kind": label,
                    "id": int(row["id"]),
                    "name": row["name"],
                    "image_path": row["image_path"],
                    "entity": table,
                }
                for row in rows
            )
    for kind, (label, _) in ASSET_KINDS.items():
        results.extend(
            {
                "kind": label,
                "id": None,
                "name": entry["name"],
                "image_path": entry["path"],
                "entity": None,
            }
            for entry in list_assets(kind)
            if normalized_query in normalize_player_name(entry["name"])
        )
    # O mesmo emblema pode vir do clube e do arquivo de logo: fica um só, com imagem.
    unique: dict[tuple[str, str], dict[str, Any]] = {}
    for item in results:
        key = (item["kind"], normalize_player_name(item["name"]))
        if key not in unique or (item["image_path"] and not unique[key]["image_path"]):
            unique[key] = item
    results = sorted(
        unique.values(),
        key=lambda item: (
            normalize_player_name(item["name"]) != normalized_query,
            normalize_player_name(item["name"]),
        ),
    )
    return results[:limit]


def claim_player(
    player_id: int,
    discord_user_id: int | str,
    database_path=DATABASE_PATH,
) -> bool:
    """Entrega um jogador uma única vez; a chave primária evita posse duplicada."""
    try:
        with connect(database_path) as connection:
            connection.execute(
                """
                INSERT INTO player_ownership (player_id, discord_user_id)
                VALUES (?, ?)
                """,
                (player_id, str(discord_user_id)),
            )
    except sqlite3.IntegrityError as error:
        if "player_ownership.player_id" in str(error) or "UNIQUE constraint" in str(error):
            return False
        raise
    return True


# Siglas e palavras genéricas ignoradas ao comparar o nome do arquivo com o clube.
_CLUB_AFFIXES = {
    "fc",
    "cf",
    "sc",
    "fk",
    "ac",
    "afc",
    "cd",
    "club",
    "clube",
    "futebol",
    "football",
    "calcio",
    "sv",
    "ec",
    "se",
    "cr",
    "sl",
    "ca",
    "sk",
    "nk",
    "bk",
    "ss",
    "as",
    "us",
    "rc",
    "rcd",
    "ud",
    "sd",
    "de",
    "da",
    "do",
    "the",
    "1",
    "fbc",
    "cfc",
    "ssc",
}


def _club_key(name: str) -> str:
    words = normalize_player_name(name).split()
    core = [word for word in words if word not in _CLUB_AFFIXES]
    return " ".join(core or words)


def sync_club_logos(database_path=DATABASE_PATH) -> dict[str, int]:
    """Liga cada escudo/logo local a um clube; clubes que faltam no banco são criados.

    Assim times que só existem na pasta de logos (ex.: Shakhtar Donetsk) aparecem no
    `mm!scout` e no `mm!top clubes`.
    """
    report = {"linked": 0, "created": 0}
    entries = list_assets("emblema") + list_assets("logo_cartola")
    if not entries:
        return report
    with connect(database_path) as connection:
        clubs: dict[str, list[dict[str, Any]]] = {}
        for row in connection.execute("SELECT id, name, logo_path FROM clubs"):
            clubs.setdefault(_club_key(row["name"]), []).append(dict(row))
        for entry in entries:
            path = Path(entry["path"]).as_posix()
            matches = clubs.get(_club_key(entry["name"]), [])
            if len(matches) == 1:
                club = matches[0]
                if not club["logo_path"]:
                    connection.execute(
                        "UPDATE clubs SET logo_path = ? WHERE id = ?", (path, club["id"])
                    )
                    club["logo_path"] = path
                    report["linked"] += 1
                continue
            if matches:
                continue  # nome ambíguo: melhor não adivinhar
            club_id = connection.execute(
                "INSERT INTO clubs (name, logo_path) VALUES (?, ?)", (entry["name"], path)
            ).lastrowid
            clubs[_club_key(entry["name"])] = [
                {"id": club_id, "name": entry["name"], "logo_path": path}
            ]
            report["created"] += 1
    return report


def resolve_player(
    query: str,
    players: list[dict[str, Any]],
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """Escolhe o jogador pelo nome; o ID só é necessário quando há homônimos.

    Ordem: ID exato, nome exato (sem acento), apelido contido no nome
    (`kvara` acha Kvaratskhelia) e iniciais das palavras. Devolve `(jogador, [])`
    quando só um serve, ou `(None, candidatos)` para a pessoa escolher.
    """
    text = query.strip()
    if not text:
        return None, []
    if text.isdigit():
        found = [player for player in players if int(player["id"]) == int(text)]
        return (found[0], []) if found else (None, [])
    wanted = normalize_player_name(text)

    def names(player: dict[str, Any]) -> list[str]:
        return [
            normalize_player_name(str(player.get(key) or ""))
            for key in ("name", "display_name")
            if player.get(key)
        ]

    exact = [player for player in players if wanted in names(player)]
    if len(exact) == 1:
        return exact[0], []
    if exact:
        return None, exact
    tokens = wanted.split()
    partial = [
        player
        for player in players
        if any(
            wanted in name
            or all(any(word.startswith(token) for word in name.split()) for token in tokens)
            for name in names(player)
        )
    ]
    if len(partial) == 1:
        return partial[0], []
    partial.sort(key=lambda player: -(player.get("overall") or 0))
    return None, partial


def find_player(
    query: str,
    database_path=DATABASE_PATH,
    limit: int = 25,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """`resolve_player` sobre o banco inteiro (wish, scout…)."""
    return resolve_player(query, search_players(query, database_path, limit=limit))
