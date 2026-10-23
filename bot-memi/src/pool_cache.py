"""Índice em memória do mercado de jogadores (roll, top e busca).

O overall de jogo é calculado em Python (`src.ratings.game_overall`) e, dentro do SQL, vira
uma função chamada linha a linha: com dezenas de milhares de jogadores, cada roll, top ou
busca varria a tabela inteira. Aqui o mercado é lido uma vez e guardado com o overall de
jogo, a série de raridade e os nomes normalizados de cada jogador.

O índice é refeito sozinho quando a tabela `players` muda: a cada uso, uma assinatura
barata (contagem, maior id e somas das colunas que importam) é comparada com a guardada.
Posse, bolas e wishes não entram no índice; esses dados continuam vindo do banco na hora.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path

from src.db import normalize_player_name
from src.ratings import POOL_SQL, game_overall, rarity_index

SIGNATURE_SQL = """
    SELECT COUNT(*), MAX(id), TOTAL(overall), TOTAL(LENGTH(name)),
           TOTAL(LENGTH(display_name)), TOTAL(LENGTH(activity_status)),
           TOTAL(LENGTH(game_position)), TOTAL(LENGTH(overall_source))
    FROM players
"""


@dataclass(frozen=True)
class PoolSnapshot:
    signature: tuple
    overall: dict[int, int]  # id → overall de jogo (sem bolas)
    bands: dict[int, list[int]]  # série de raridade → ids do mercado, em ordem de id
    ranking: list[int]  # ids do mercado do maior overall para o menor (nome, id)
    # id → (nome normalizado, apelido normalizado, nome original), de todos os jogadores
    names: dict[int, tuple[str, str, str]]


_lock = threading.Lock()
_snapshots: dict[str, PoolSnapshot] = {}


def snapshot(connection, database_path: str | Path) -> PoolSnapshot:
    """O índice atual do banco; refeito só se a tabela de jogadores mudou."""
    signature = tuple(connection.execute(SIGNATURE_SQL).fetchone())
    key = str(Path(database_path).resolve())
    with _lock:
        cached = _snapshots.get(key)
        if cached is not None and cached.signature == signature:
            return cached
    rows = connection.execute(f"""
        SELECT p.id, p.name, p.display_name, p.overall, p.overall_source,
               {POOL_SQL} AS in_pool
        FROM players AS p
        """).fetchall()
    overall: dict[int, int] = {}
    bands: dict[int, list[int]] = {}
    names: dict[int, tuple[str, str, str]] = {}
    pool: list[tuple[int, str, int]] = []
    for row in rows:
        player_id = int(row["id"])
        name = str(row["name"] or "")
        names[player_id] = (
            normalize_player_name(name),
            normalize_player_name(str(row["display_name"] or "")),
            name,
        )
        value = game_overall(row["overall"], row["overall_source"], name)
        overall[player_id] = value
        if row["in_pool"]:
            bands.setdefault(rarity_index(value), []).append(player_id)
            label = str(row["display_name"] or "") or name
            pool.append((value, label.casefold(), player_id))
    for ids in bands.values():
        ids.sort()
    pool.sort(key=lambda item: (-item[0], item[1], item[2]))
    built = PoolSnapshot(signature, overall, bands, [item[2] for item in pool], names)
    with _lock:
        _snapshots[key] = built
    return built


def ordered_rows(connection, ids: list[int], columns: str, joins: str) -> list[dict]:
    """Busca as linhas dos ids e devolve na mesma ordem da lista."""
    if not ids:
        return []
    marks = ", ".join("?" for _ in ids)
    rows = connection.execute(f"SELECT {columns} {joins} WHERE p.id IN ({marks})", ids).fetchall()
    by_id = {int(row["id"]): dict(row) for row in rows}
    return [by_id[player_id] for player_id in ids if player_id in by_id]
