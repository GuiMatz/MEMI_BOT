"""Pacote de jogador: paga Memilets e recebe uma carta livre do mercado.

O pacote nunca traz Base. A carta vai direto para o elenco, marcada como "veio de
pacote": pode ser vendida na hora por `PACK_SALE_FACTOR` do valor e passa `PACK_LOCK`
sem poder ser doada ou trocada (`src.transactions`). Em média um pacote vende por ~70% do
preço: comprar para revender nunca dá lucro.
Se não houver jogador livre na série sorteada, tenta a série de baixo e depois as de cima.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from src.db import DATABASE_PATH, connect
from src.game import register_user
from src.missions import record
from src.ratings import EFFECTIVE_OVERALL_SQL, GAME_OVERALL_SQL, POOL_SQL, RARITY_TIERS
from src.transactions import PACK_LOCK, _utc_now

PACK_PRICE = 1_500
# Série de raridade (índice em RARITY_TIERS) → chance. Soma 1.
PACK_ODDS = {1: 0.40, 2: 0.35, 3: 0.185, 4: 0.05, 5: 0.015}
# Pacote épico: só Raro ou melhor, com chance de Ícone. Vendendo na hora (50%),
# rende em média ~83% do preço (o comum, ~86%).
EPIC_PACK_PRICE = 5_000
EPIC_PACK_ODDS = {3: 0.45, 4: 0.35, 5: 0.17, 6: 0.03}


class PackError(Exception):
    """Pacote recusado (saldo ou mercado vazio); a mensagem vai para a pessoa."""


@dataclass(frozen=True)
class PackResult:
    player: dict[str, Any]
    price: int
    locked_until: datetime


def pack_odds_text(epic: bool = False) -> list[tuple[str, str, float]]:
    """(emoji, rótulo, chance) de cada série, para o menu."""
    odds = EPIC_PACK_ODDS if epic else PACK_ODDS
    return [(RARITY_TIERS[i][1], RARITY_TIERS[i][0], chance) for i, chance in odds.items()]


def _free_player(connection, tier: int, rng) -> dict[str, Any] | None:
    where = (
        f"{POOL_SQL} AND rarity_index({GAME_OVERALL_SQL}) = ? AND NOT EXISTS "
        "(SELECT 1 FROM player_ownership AS o WHERE o.player_id = p.id)"
    )
    total = connection.execute(
        f"SELECT COUNT(*) AS n FROM players AS p WHERE {where}", (tier,)
    ).fetchone()["n"]
    if not total:
        return None
    row = connection.execute(
        f"""
        SELECT p.*, {EFFECTIVE_OVERALL_SQL} AS effective_overall
        FROM players AS p LEFT JOIN player_balls AS b ON b.player_id = p.id
        WHERE {where} ORDER BY p.id LIMIT 1 OFFSET ?
        """,
        (tier, rng.randrange(total)),
    ).fetchone()
    return dict(row)


def open_pack(
    discord_user_id: int | str,
    display_name: str,
    database_path: str | Path = DATABASE_PATH,
    *,
    now: datetime | None = None,
    rng: random.Random | random.SystemRandom | None = None,
    epic: bool = False,
) -> PackResult:
    """Abre um pacote (`epic=True`: o pacote épico, mais caro e só Raro ou melhor)."""
    user_id = str(discord_user_id)
    generator = rng or random.SystemRandom()
    current_time = _utc_now(now)
    register_user(user_id, display_name, database_path)
    odds = EPIC_PACK_ODDS if epic else PACK_ODDS
    price = EPIC_PACK_PRICE if epic else PACK_PRICE
    tier = generator.choices(list(odds), weights=list(odds.values()), k=1)[0]
    # A série sorteada primeiro; sem ninguém livre, as de baixo e depois as de cima
    # (sempre dentro das séries do pacote: o épico nunca cai para Comum).
    order = [tier] + sorted((t for t in odds if t < tier), reverse=True)
    order += sorted(t for t in odds if t > tier)
    with connect(database_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        balance = connection.execute(
            "SELECT memilets FROM game_users WHERE discord_user_id = ?", (user_id,)
        ).fetchone()["memilets"]
        if balance < price:
            raise PackError(
                (f"Um pacote {'épico ' if epic else ''}custa {price:,} Memilets.").replace(",", ".")
            )
        player = next(
            (found for t in order if (found := _free_player(connection, t, generator))), None
        )
        if player is None:
            raise PackError("Não há jogadores livres no mercado agora. Tente mais tarde.")
        locked_until = current_time + PACK_LOCK
        connection.execute(
            "UPDATE game_users SET memilets = memilets - ?, updated_at = CURRENT_TIMESTAMP "
            "WHERE discord_user_id = ?",
            (price, user_id),
        )
        connection.execute(
            """
            INSERT INTO player_ownership (player_id, discord_user_id, pack_origin, pack_locked_until)
            VALUES (?, ?, 1, ?)
            """,
            (player["id"], user_id, locked_until.isoformat(timespec="seconds")),
        )
        record(connection, user_id, "pack", now=current_time)
    return PackResult(player, price, locked_until)
