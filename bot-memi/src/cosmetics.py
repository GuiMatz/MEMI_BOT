"""Cosméticos do clube: emblema, uniforme, estádio, técnico, liga e mascote.

Só dá para equipar o que a conta possui. Cosméticos saem nas crates (`mm!crates`) e
nunca repetem um item que a pessoa já tem. Clubes e logos são a mesma coisa: os dois
viram **emblemas**.

Uniforme, estádio, técnico e mascote são **únicos**, como as cartas de jogador: cada item
tem um dono só, e a caixa só sorteia o que ainda não é de ninguém. Emblema e liga podem
se repetir entre times.

Cada cosmético equipado, menos o estádio, soma +100 ao máximo do daily; o estádio
libera a renda do amistoso (`src.income`).
"""

from __future__ import annotations

import random
import re
import sqlite3
from pathlib import Path
from typing import Any

from src.db import DATABASE_PATH, connect, normalize_player_name
from src.game import list_assets, pretty_asset_name, register_user


class CosmeticError(Exception):
    """Erro de posse ou de escolha de cosmético que pode ser mostrado ao jogador."""


# tipo: (rótulo, emoji, coluna em game_teams, guarda o caminho da imagem em vez do nome)
COSMETIC_KINDS: dict[str, tuple[str, str, str, bool]] = {
    "emblema": ("Emblema", "🛡️", "logo_path", True),
    "uniforme": ("Uniforme", "👕", "kit_path", True),
    "estadio": ("Estádio", "🏟️", "stadium_name", False),
    "tecnico": ("Técnico", "🧑‍💼", "coach_name", False),
    "liga": ("Liga", "🏆", "favorite_league", False),
    "mascote": ("Mascote", "🐾", "mascot", False),
}

# Tipos com dono único (um item, uma pessoa), como as cartas.
UNIQUE_KINDS = frozenset({"uniforme", "estadio", "tecnico", "mascote"})
# Compensação, em Memilets, de quem perdeu um item repetido quando eles viraram únicos.
DUPLICATE_REFUND = 2_500

# Como cada tipo aparece nos comandos (`mm!time tecnico X`, `mm!emblema X`…).
KIND_ALIASES = {
    "emblema": "emblema",
    "emblemas": "emblema",
    "logo": "emblema",
    "logos": "emblema",
    "escudo": "emblema",
    "clube": "emblema",
    "uniforme": "uniforme",
    "uniformes": "uniforme",
    "camisa": "uniforme",
    "kit": "uniforme",
    "estadio": "estadio",
    "estadios": "estadio",
    "tecnico": "tecnico",
    "tecnicos": "tecnico",
    "treinador": "tecnico",
    "liga": "liga",
    "ligas": "liga",
    "mascote": "mascote",
    "mascotes": "mascote",
}

# Apelidos comuns trocados pelo nome oficial antes de procurar.
NICKNAMES = {
    "brasileirao": "campeonato brasileiro",
    "carioca": "campeonato carioca",
    "paulistao": "campeonato paulista",
    "gauchao": "campeonato gaucho",
    "mineirao": "campeonato mineiro",
    "baianao": "campeonato baiano",
    "mls": "major league soccer",
    "epl": "premier league",
}


def item_key(name: str) -> str:
    return normalize_player_name(name)


def catalog(kind: str, database_path: str | Path = DATABASE_PATH) -> list[dict[str, Any]]:
    """Todos os itens de um tipo: `{"key", "name", "image_path"}`, sem repetir nome."""
    if kind not in COSMETIC_KINDS:
        raise ValueError(f"Tipo de cosmético desconhecido: {kind}")
    items: list[dict[str, Any]] = []
    if kind == "emblema":
        for entry in list_assets("emblema") + list_assets("logo_cartola"):
            items.append({"name": entry["name"], "image_path": Path(entry["path"]).as_posix()})
        # Escudos baixados do TheSportsDB (data/photos/clubs/<id>.png) também são emblemas.
        with connect(database_path) as connection:
            items += [
                dict(row)
                for row in connection.execute(
                    "SELECT name, logo_path AS image_path FROM clubs "
                    "WHERE logo_path LIKE 'clubs/%'"
                )
            ]
    elif kind == "uniforme":
        items = [
            {"name": entry["name"], "image_path": Path(entry["path"]).as_posix()}
            for entry in list_assets("uniforme")
        ]
    else:
        queries = {
            "estadio": "SELECT name, photo_path AS image_path FROM stadiums",
            "tecnico": "SELECT name, photo_path AS image_path FROM coaches",
            "liga": "SELECT name, logo_path AS image_path FROM leagues",
            "mascote": (
                "SELECT name || COALESCE(' (' || owner || ')', '') AS name, "
                "image_path FROM mascots"
            ),
        }
        with connect(database_path) as connection:
            items = [dict(row) for row in connection.execute(queries[kind])]
        if kind == "mascote":
            items += [
                {"name": entry["name"], "image_path": Path(entry["path"]).as_posix()}
                for entry in list_assets("mascote")
            ]
    unique: dict[str, dict[str, Any]] = {}
    for item in items:
        key = item_key(item["name"])
        if key and key not in unique:
            unique[key] = {"key": key, **item}
    return sorted(unique.values(), key=lambda item: item["key"])


def list_owned(
    discord_user_id: int | str,
    kind: str | None = None,
    database_path: str | Path = DATABASE_PATH,
) -> list[dict[str, Any]]:
    with connect(database_path) as connection:
        rows = connection.execute(
            """
            SELECT kind, item_key AS key, name, image_path, source, acquired_at
            FROM user_cosmetics
            WHERE discord_user_id = ? AND (? IS NULL OR kind = ?)
            ORDER BY kind, name COLLATE NOCASE
            """,
            (str(discord_user_id), kind, kind),
        ).fetchall()
    return [dict(row) for row in rows]


def grant(
    connection: sqlite3.Connection,
    discord_user_id: int | str,
    kind: str,
    item: dict[str, Any],
    source: str = "crate",
) -> bool:
    """Entrega o item; devolve False se a pessoa já o tinha ou se é de dono único e já
    pertence a outra pessoa."""
    key = item.get("key") or item_key(item["name"])
    if (
        kind in UNIQUE_KINDS
        and connection.execute(
            "SELECT 1 FROM user_cosmetics WHERE kind = ? AND item_key = ? AND discord_user_id != ?",
            (kind, key, str(discord_user_id)),
        ).fetchone()
    ):
        return False
    cursor = connection.execute(
        """
        INSERT OR IGNORE INTO user_cosmetics
            (discord_user_id, kind, item_key, name, image_path, source)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            str(discord_user_id),
            kind,
            key,
            item["name"],
            item.get("image_path"),
            source,
        ),
    )
    return cursor.rowcount > 0


def unowned_items(
    connection: sqlite3.Connection,
    discord_user_id: int | str,
    kind: str,
    database_path: str | Path = DATABASE_PATH,
) -> list[dict[str, Any]]:
    if kind in UNIQUE_KINDS:
        # Dono único: some do sorteio assim que alguém tem.
        query, arguments = "SELECT item_key FROM user_cosmetics WHERE kind = ?", (kind,)
    else:
        query = "SELECT item_key FROM user_cosmetics WHERE discord_user_id = ? AND kind = ?"
        arguments = (str(discord_user_id), kind)
    owned = {row["item_key"] for row in connection.execute(query, arguments)}
    return [item for item in catalog(kind, database_path) if item["key"] not in owned]


def random_new_item(
    connection: sqlite3.Connection,
    discord_user_id: int | str,
    rng: random.Random | random.SystemRandom,
    database_path: str | Path = DATABASE_PATH,
) -> tuple[str, dict[str, Any]] | None:
    """Sorteia um tipo e um item que a pessoa ainda não tem (ou None se já tem tudo)."""
    kinds = list(COSMETIC_KINDS)
    rng.shuffle(kinds)
    for kind in kinds:
        options = unowned_items(connection, discord_user_id, kind, database_path)
        if options:
            return kind, rng.choice(options)
    return None


def _wanted(query: str) -> str:
    wanted = normalize_player_name(query)
    for nickname, official in NICKNAMES.items():
        wanted = re.sub(rf"\b{nickname}\b", official, wanted)
    if wanted == "campeonato brasileiro":
        wanted += " serie a"
    return wanted


def match_items(query: str, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Nome exato (sem acento) primeiro; senão, itens cujas palavras começam com o texto."""
    wanted = _wanted(query)
    if not wanted:
        return []
    exact = [item for item in items if item_key(item["name"]) == wanted]
    if exact:
        return exact
    tokens = wanted.split()
    return [
        item
        for item in items
        if wanted in item_key(item["name"])
        or all(
            any(word.startswith(token) for word in item_key(item["name"]).split())
            for token in tokens
        )
    ]


def equip(
    discord_user_id: int | str,
    kind: str,
    query: str,
    database_path: str | Path = DATABASE_PATH,
) -> dict[str, Any]:
    """Equipa um cosmético que a pessoa possui e devolve o item escolhido."""
    label, _, column, stores_path = COSMETIC_KINDS[kind]
    owned = list_owned(discord_user_id, kind, database_path)
    found = match_items(query, owned)
    if not found:
        exists = match_items(query, catalog(kind, database_path))
        if exists:
            raise CosmeticError(
                f"Você ainda não tem **{exists[0]['name']}**. Cosméticos saem nas crates "
                "(`mm!crates`) e só dá para equipar o que é seu."
            )
        if not owned:
            raise CosmeticError(
                f"Você ainda não tem nenhum {label.lower()}. Abra crates com `mm!crates`."
            )
        raise CosmeticError(
            f"Não achei **{query[:60]}** entre os seus {label.lower()}s. "
            f"Veja os seus com `mm!cosmeticos {kind}`."
        )
    if len(found) > 1:
        names = ", ".join(item["name"] for item in found[:8])
        raise CosmeticError(f"Mais de um {label.lower()} seu combina: {names}.")
    item = found[0]
    value = item["image_path"] if stores_path else item["name"]
    if stores_path and not value:
        raise CosmeticError(f"{item['name']} não tem imagem local para equipar.")
    with connect(database_path) as connection:
        cursor = connection.execute(
            f"UPDATE game_teams SET {column} = ?, updated_at = CURRENT_TIMESTAMP "
            "WHERE discord_user_id = ?",
            (value, str(discord_user_id)),
        )
        if cursor.rowcount == 0:
            raise CosmeticError("Seu time ainda não foi criado. Use `mm!time` primeiro.")
    return item


def equip_key(
    discord_user_id: int | str,
    kind: str,
    key: str,
    database_path: str | Path = DATABASE_PATH,
) -> dict[str, Any]:
    """Equipa pelo identificador exato (menus do `mm!personalizar`), sem busca por nome."""
    label, _, column, stores_path = COSMETIC_KINDS[kind]
    item = next(
        (item for item in list_owned(discord_user_id, kind, database_path) if item["key"] == key),
        None,
    )
    if item is None:
        raise CosmeticError(f"Esse {label.lower()} não é seu.")
    value = item["image_path"] if stores_path else item["name"]
    if stores_path and not value:
        raise CosmeticError(f"{item['name']} não tem imagem local para equipar.")
    with connect(database_path) as connection:
        connection.execute(
            f"UPDATE game_teams SET {column} = ?, updated_at = CURRENT_TIMESTAMP "
            "WHERE discord_user_id = ?",
            (value, str(discord_user_id)),
        )
    return item


def unequip(
    discord_user_id: int | str, kind: str, database_path: str | Path = DATABASE_PATH
) -> None:
    column = COSMETIC_KINDS[kind][2]
    with connect(database_path) as connection:
        connection.execute(
            f"UPDATE game_teams SET {column} = NULL, updated_at = CURRENT_TIMESTAMP "
            "WHERE discord_user_id = ?",
            (str(discord_user_id),),
        )


def equipped_name(
    kind: str, value: str | None, database_path: str | Path = DATABASE_PATH
) -> str | None:
    """Nome legível do que está equipado (emblema e uniforme guardam o caminho)."""
    if not value:
        return None
    if not COSMETIC_KINDS[kind][3]:
        return value
    wanted = Path(value).as_posix()
    for item in catalog(kind, database_path):
        if item.get("image_path") == wanted:
            return item["name"]
    return pretty_asset_name(Path(value).stem)


def free_counts(database_path: str | Path = DATABASE_PATH) -> dict[str, tuple[int, int]]:
    """`{tipo: (sem dono, total)}` para o `mm!left`."""
    with connect(database_path) as connection:
        owned: dict[str, set[str]] = {}
        for row in connection.execute("SELECT DISTINCT kind, item_key FROM user_cosmetics"):
            owned.setdefault(row["kind"], set()).add(row["item_key"])
    counts = {}
    for kind in COSMETIC_KINDS:
        items = catalog(kind, database_path)
        taken = owned.get(kind, set())
        counts[kind] = (sum(1 for item in items if item["key"] not in taken), len(items))
    return counts


# Rótulo usado no `mm!scout` -> tipo de cosmético.
LABEL_TO_KIND = {label: kind for kind, (label, *_) in COSMETIC_KINDS.items()}


def owners_of(kind: str, name: str, database_path: str | Path = DATABASE_PATH) -> list[str]:
    """Nomes de quem tem o cosmético (para a ficha do `mm!scout`)."""
    with connect(database_path) as connection:
        rows = connection.execute(
            """
            SELECT u.display_name FROM user_cosmetics AS c
            JOIN game_users AS u ON u.discord_user_id = c.discord_user_id
            WHERE c.kind = ? AND c.item_key = ?
            ORDER BY c.acquired_at
            """,
            (kind, item_key(name)),
        ).fetchall()
    return [row["display_name"] for row in rows]


def refresh_names(database_path: str | Path = DATABASE_PATH) -> int:
    """Atualiza o nome de cosméticos com imagem quando a regra de nomes muda."""
    changed = 0
    with connect(database_path) as connection:
        for kind in ("emblema", "uniforme", "mascote"):
            by_path = {
                item["image_path"]: item
                for item in catalog(kind, database_path)
                if item.get("image_path")
            }
            rows = connection.execute(
                "SELECT discord_user_id, item_key, name, image_path FROM user_cosmetics "
                "WHERE kind = ? AND image_path IS NOT NULL",
                (kind,),
            ).fetchall()
            for row in rows:
                item = by_path.get(row["image_path"])
                if item is None or item["name"] == row["name"]:
                    continue
                cursor = connection.execute(
                    "UPDATE OR IGNORE user_cosmetics SET name = ?, item_key = ? "
                    "WHERE discord_user_id = ? AND kind = ? AND item_key = ?",
                    (item["name"], item["key"], row["discord_user_id"], kind, row["item_key"]),
                )
                changed += cursor.rowcount
    return changed


def resolve_unique_duplicates(database_path: str | Path = DATABASE_PATH) -> int:
    """Itens de dono único que ficaram com mais de um dono (da regra antiga).

    Fica com quem pegou primeiro; os outros perdem o item (desequipa, se estava em uso) e
    recebem `DUPLICATE_REFUND` Memilets por item. Devolve quantos itens foram tirados.
    Sem repetidos, não faz nada: dá para rodar toda vez que o bot liga."""
    removed = 0
    with connect(database_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        for kind in sorted(UNIQUE_KINDS):
            _, _, column, stores_path = COSMETIC_KINDS[kind]
            rows = connection.execute(
                """
                SELECT rowid, discord_user_id, item_key, name, image_path FROM user_cosmetics
                WHERE kind = ? AND item_key IN (
                    SELECT item_key FROM user_cosmetics WHERE kind = ?
                    GROUP BY item_key HAVING COUNT(*) > 1
                )
                ORDER BY item_key, acquired_at, rowid
                """,
                (kind, kind),
            ).fetchall()
            first_owner: set[str] = set()
            for row in rows:
                if row["item_key"] not in first_owner:
                    first_owner.add(row["item_key"])  # o mais antigo fica
                    continue
                user_id = row["discord_user_id"]
                connection.execute("DELETE FROM user_cosmetics WHERE rowid = ?", (row["rowid"],))
                value = row["image_path"] if stores_path else row["name"]
                connection.execute(
                    f"UPDATE game_teams SET {column} = NULL "
                    f"WHERE discord_user_id = ? AND {column} = ?",
                    (user_id, value),
                )
                connection.execute(
                    "UPDATE game_users SET memilets = memilets + ? WHERE discord_user_id = ?",
                    (DUPLICATE_REFUND, user_id),
                )
                removed += 1
    return removed


def grandfather_equipped(database_path: str | Path = DATABASE_PATH) -> int:
    """Quem já tinha algo equipado antes da regra de posse passa a possuir esse item."""
    granted = 0
    with connect(database_path) as connection:
        teams = [dict(row) for row in connection.execute("SELECT * FROM game_teams")]
        for team in teams:
            for kind, (_, _, column, stores_path) in COSMETIC_KINDS.items():
                value = team.get(column)
                if not value:
                    continue
                item = {
                    "name": equipped_name(kind, value, database_path),
                    "image_path": value if stores_path else None,
                }
                granted += grant(connection, team["discord_user_id"], kind, item, "legado")
    return granted


def give_item(
    discord_user_id: int | str,
    display_name: str,
    kind: str,
    query: str,
    database_path: str | Path = DATABASE_PATH,
) -> dict[str, Any]:
    """Entrega um item do catálogo (uso administrativo)."""
    found = match_items(query, catalog(kind, database_path))
    if len(found) != 1:
        raise CosmeticError(
            "Nenhum item combina." if not found else f"{len(found)} itens combinam."
        )
    register_user(discord_user_id, display_name, database_path)
    with connect(database_path) as connection:
        if not grant(connection, discord_user_id, kind, found[0], "admin") and kind in UNIQUE_KINDS:
            owner = owners_of(kind, found[0]["name"], database_path)
            if owner and owner != [display_name]:
                raise CosmeticError(f"{found[0]['name']} já é de {owner[0]} (item único).")
    return found[0]
