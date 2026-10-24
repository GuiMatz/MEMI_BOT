"""Ferramentas dos admins (`mm!admin ...`): Memilets, itens, jogadores, rolls e backup.

Tudo aqui mexe direto no banco, sem as regras do jogo (preço, limite de elenco, cooldown):
é para compensar alguém, corrigir um erro ou testar. Quem chama confere se é admin.
"""

from __future__ import annotations

import gzip
from contextlib import closing
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.db import DATABASE_PATH, connect
from src.game import find_player, register_user
from src.inventory import INVENTORY_ITEMS, give
from src.rolls import cycle_start_for
from src.transactions import _move_player

BACKUP_KEEP = 10


class AdminError(Exception):
    """Pedido de admin que não dá para cumprir; a mensagem vai para quem pediu."""


def adjust_memilets(
    user_id: int | str, name: str, amount: int, database_path=DATABASE_PATH
) -> tuple[int, int]:
    """Soma (ou tira, com `amount` negativo) Memilets; o saldo não fica negativo.

    Devolve (quanto mudou de fato, saldo novo)."""
    register_user(user_id, name, database_path)
    with connect(database_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        balance = connection.execute(
            "SELECT memilets FROM game_users WHERE discord_user_id = ?", (str(user_id),)
        ).fetchone()["memilets"]
        change = max(amount, -balance)
        connection.execute(
            "UPDATE game_users SET memilets = memilets + ?, updated_at = CURRENT_TIMESTAMP "
            "WHERE discord_user_id = ?",
            (change, str(user_id)),
        )
    return change, balance + change


def item_key(text: str) -> str:
    """O item pelo nome do inventário ("caixa épica", "caixa_epica", "pacote")."""
    wanted = text.strip().casefold().replace(" ", "_").replace("é", "e")
    for key, (_, label) in INVENTORY_ITEMS.items():
        if wanted in (key, label.casefold().replace(" ", "_").replace("é", "e")):
            return key
    options = ", ".join(INVENTORY_ITEMS)
    raise AdminError(f"Item desconhecido. Use um destes: {options}.")


def adjust_items(
    user_id: int | str, name: str, item: str, amount: int, database_path=DATABASE_PATH
) -> int:
    """Dá (ou tira, com `amount` negativo) itens do inventário; devolve quantos ficaram."""
    register_user(user_id, name, database_path)
    with connect(database_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        if amount > 0:
            give(connection, str(user_id), item, amount)
        else:
            connection.execute(
                "UPDATE user_inventory SET amount = MAX(0, amount + ?) "
                "WHERE discord_user_id = ? AND item = ?",
                (amount, str(user_id), item),
            )
        row = connection.execute(
            "SELECT amount FROM user_inventory WHERE discord_user_id = ? AND item = ?",
            (str(user_id), item),
        ).fetchone()
    return int(row["amount"]) if row else 0


def reset_rolls(
    user_id: int | str, database_path=DATABASE_PATH, *, now: datetime | None = None
) -> None:
    """Devolve os rolls do ciclo atual (como se ainda não tivesse rolado nesse ciclo)."""
    cycle = cycle_start_for(now or datetime.now(timezone.utc)).isoformat(timespec="seconds")
    with connect(database_path) as connection:
        connection.execute(
            "DELETE FROM roll_cycles WHERE discord_user_id = ? AND cycle_started_at = ?",
            (str(user_id), cycle),
        )


def reset_claim(user_id: int | str, database_path=DATABASE_PATH) -> None:
    """Libera o claim (contratação) na hora."""
    with connect(database_path) as connection:
        connection.execute(
            "UPDATE game_users SET last_claim_at = NULL, updated_at = CURRENT_TIMESTAMP "
            "WHERE discord_user_id = ?",
            (str(user_id),),
        )


@dataclass(frozen=True)
class GivenPlayer:
    player: dict[str, Any]
    previous_owner: str | None  # de quem saiu (com `force`), ou None se estava livre


def give_player(
    query: str,
    user_id: int | str,
    name: str,
    database_path=DATABASE_PATH,
    *,
    force: bool = False,
) -> GivenPlayer:
    """Entrega um jogador (nome ou ID). Se ele já tem dono, só com `force` (sai do time do
    dono antigo, como numa troca)."""
    player, candidates = find_player(query, database_path)
    if player is None:
        if not candidates:
            raise AdminError(f"Não achei nenhum jogador com `{query[:40]}`.")
        listed = "\n".join(
            f"`{p['id']}` {p.get('display_name') or p['name']} ({p['overall']}, "
            f"{p.get('club_name') or 'sem clube'})"
            for p in candidates[:8]
        )
        raise AdminError(f"Mais de um jogador serve. Use o ID:\n{listed}")
    target = str(user_id)
    register_user(user_id, name, database_path)
    owner = player.get("owner_id")
    if owner == target:
        raise AdminError("Esse jogador já é dessa pessoa.")
    with connect(database_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        if owner is None:
            connection.execute(
                "INSERT INTO player_ownership (player_id, discord_user_id) VALUES (?, ?)",
                (int(player["id"]), target),
            )
        elif force:
            _move_player(connection, int(player["id"]), str(owner), target)
        else:
            raise AdminError(
                f"Esse jogador é de <@{owner}>. Para tirar de lá e entregar mesmo assim, "
                "repita o comando com `forçar` no fim."
            )
    return GivenPlayer(player, owner if owner else None)


# Backup e situação ------------------------------------------------------------------------


def backup_folder(database_path=DATABASE_PATH) -> Path:
    return Path(database_path).parent / "backups"


def backup_database(database_path=DATABASE_PATH, *, now: datetime | None = None) -> Path:
    """Cópia consistente do banco do jogo, comprimida, em `backups/` ao lado dele.

    Guarda as últimas `BACKUP_KEEP` (as mais antigas saem)."""
    folder = backup_folder(database_path)
    folder.mkdir(parents=True, exist_ok=True)
    moment = (now or datetime.now(timezone.utc)).strftime("%Y%m%d-%H%M%S")
    raw = folder / f"jogo-{moment}.sqlite"
    # `closing`: o `with` do sqlite3 só faz commit; sem fechar, o Windows não deixa apagar a
    # cópia sem compressão logo depois.
    with connect(database_path) as source, closing(sqlite3.connect(raw)) as copy:
        source.backup(copy)
    target = raw.with_suffix(".sqlite.gz")
    with raw.open("rb") as plain, gzip.open(target, "wb") as packed:
        shutil.copyfileobj(plain, packed)
    raw.unlink()
    for old in sorted(folder.glob("jogo-*.sqlite.gz"))[:-BACKUP_KEEP]:
        old.unlink()
    return target


def last_backup(database_path=DATABASE_PATH) -> Path | None:
    backups = sorted(backup_folder(database_path).glob("jogo-*.sqlite.gz"))
    return backups[-1] if backups else None


def database_size(database_path=DATABASE_PATH) -> int:
    """Tamanho do banco em bytes (com o WAL)."""
    path = Path(database_path)
    return sum(
        file.stat().st_size for file in (path, path.with_name(path.name + "-wal")) if file.is_file()
    )
