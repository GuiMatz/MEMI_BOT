"""Pacote de jogador: paga Memilets e recebe uma carta livre do mercado.

O pacote nunca traz Base. A carta vai direto para o elenco, marcada como "veio de
pacote": pode ser vendida na hora por `PACK_SALE_FACTOR` do valor e passa `PACK_LOCK`
sem poder ser doada ou trocada (`src.transactions`). Em média um pacote vende por ~70% do
preço: comprar para revender nunca dá lucro.
Se não houver jogador livre na série sorteada, tenta a série de baixo e depois as de cima.

O **all-in** (`open_all_in`) é a aposta: 30 mil numa roleta com 1 chance em 10 de Ícone
ou melhor; nas outras fatias, uma carta como a do pacote comum.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from src.db import DATABASE_PATH, connect
from src.game import register_user
from src.inventory import InventoryEmpty, take
from src.missions import record
from src.ratings import EFFECTIVE_OVERALL_SQL, GAME_OVERALL_SQL, POOL_SQL, RARITY_TIERS
from src.transactions import PACK_LOCK, _utc_now

PACK_PRICE = 1_500
# Série de raridade (índice em RARITY_TIERS) → chance. Soma 1.
PACK_ODDS = {1: 0.40, 2: 0.35, 3: 0.185, 4: 0.05, 5: 0.015}
# Pacote épico: só Raro ou melhor, com chance pequena de Ícone. É o pacote de quem tem
# dinheiro sobrando: vendendo na hora rende ~42% do preço.
EPIC_PACK_PRICE = 10_000
EPIC_PACK_ODDS = {3: 0.44, 4: 0.40, 5: 0.10, 6: 0.05, 7: 0.01}
# All-in: uma aposta, não um investimento. A roleta tem 20 fatias iguais e a cor de cada
# uma é a série que ela dá: 2 douradas (Ícone ou melhor, 1 em 10) e o resto carta comum.
ALL_IN_PRICE = 30_000
ALL_IN_WHEEL = (1, 2, 3, 1, 2, 6, 1, 2, 3, 4, 1, 2, 3, 1, 2, 6, 1, 2, 3, 5)
# Na fatia dourada: qual das séries do topo vem.
ALL_IN_TOP = {6: 0.75, 7: 0.20, 8: 0.05}


class PackError(Exception):
    """Pacote recusado (saldo ou mercado vazio); a mensagem vai para a pessoa."""


@dataclass(frozen=True)
class PackResult:
    player: dict[str, Any]
    price: int
    locked_until: datetime
    slot: int | None = None  # all-in: a fatia da roleta onde parou


def pack_odds_text(epic: bool = False) -> list[tuple[str, str, float]]:
    """(emoji, rótulo, chance) de cada série, para o menu."""
    odds = EPIC_PACK_ODDS if epic else PACK_ODDS
    return [(RARITY_TIERS[i][1], RARITY_TIERS[i][0], chance) for i, chance in odds.items()]


_FREE_WHERE = (
    f"{POOL_SQL} AND rarity_index({GAME_OVERALL_SQL}) = ? AND NOT EXISTS "
    "(SELECT 1 FROM player_ownership AS o WHERE o.player_id = p.id)"
)


def _free_count(connection, tier: int) -> int:
    return connection.execute(
        f"SELECT COUNT(*) AS n FROM players AS p WHERE {_FREE_WHERE}", (tier,)
    ).fetchone()["n"]


def _free_player(connection, tier: int, rng) -> dict[str, Any] | None:
    total = _free_count(connection, tier)
    if not total:
        return None
    row = connection.execute(
        f"""
        SELECT p.*, {EFFECTIVE_OVERALL_SQL} AS effective_overall
        FROM players AS p LEFT JOIN player_balls AS b ON b.player_id = p.id
        WHERE {_FREE_WHERE} ORDER BY p.id LIMIT 1 OFFSET ?
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
    from_inventory: bool = False,
) -> PackResult:
    """Abre um pacote (`epic=True`: o pacote épico, mais caro e só Raro ou melhor).

    `from_inventory=True`: um pacote ganho numa caixa, do `mm!inventario` (sem cobrar)."""
    generator = rng or random.SystemRandom()
    odds = EPIC_PACK_ODDS if epic else PACK_ODDS
    price = EPIC_PACK_PRICE if epic else PACK_PRICE
    tier = generator.choices(list(odds), weights=list(odds.values()), k=1)[0]
    name = f"Um pacote {'épico ' if epic else ''}"
    return _buy_card(
        discord_user_id,
        display_name,
        database_path,
        price,
        _order(tier, odds),
        name,
        now,
        generator,
        inventory_item=("pacote_epico" if epic else "pacote") if from_inventory else None,
    )


def open_all_in(
    discord_user_id: int | str,
    display_name: str,
    database_path: str | Path = DATABASE_PATH,
    *,
    now: datetime | None = None,
    rng: random.Random | random.SystemRandom | None = None,
) -> PackResult:
    """Gira a roleta do all-in. Sem Ícone (ou melhor) livre, nem gira e não cobra."""
    generator = rng or random.SystemRandom()
    slot = generator.randrange(len(ALL_IN_WHEEL))
    tier = ALL_IN_WHEEL[slot]
    if tier in ALL_IN_TOP:
        tier = generator.choices(list(ALL_IN_TOP), weights=list(ALL_IN_TOP.values()), k=1)[0]
        order = _order(tier, ALL_IN_TOP)
    else:
        order = _order(tier, PACK_ODDS)
    result = _buy_card(
        discord_user_id,
        display_name,
        database_path,
        ALL_IN_PRICE,
        order,
        "O all-in ",
        now,
        generator,
        needs=tuple(ALL_IN_TOP),
    )
    return PackResult(result.player, result.price, result.locked_until, slot)


def _order(tier: int, tiers) -> list[int]:
    """A série sorteada primeiro; sem ninguém livre, as de baixo e depois as de cima
    (sempre dentro das séries do produto: o épico nunca cai para Comum)."""
    order = [tier] + sorted((t for t in tiers if t < tier), reverse=True)
    return order + sorted(t for t in tiers if t > tier)


def _buy_card(
    discord_user_id: int | str,
    display_name: str,
    database_path: str | Path,
    price: int,
    order: list[int],
    name: str,
    now: datetime | None,
    generator,
    *,
    needs: tuple[int, ...] = (),
    inventory_item: str | None = None,
) -> PackResult:
    """Cobra `price` e entrega a primeira carta livre de `order`, marcada como de pacote.

    `needs`: séries das quais precisa haver alguém livre para a compra valer (o all-in só
    gira se o prêmio grande existir de verdade). `inventory_item`: abre um pacote ganho,
    tirando do inventário em vez de cobrar (se o mercado estiver vazio, ele volta)."""
    user_id = str(discord_user_id)
    current_time = _utc_now(now)
    register_user(user_id, display_name, database_path)
    with connect(database_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        if inventory_item:
            try:
                take(connection, user_id, inventory_item)
            except InventoryEmpty as error:
                raise PackError(str(error)) from None
            price = 0
        balance = connection.execute(
            "SELECT memilets FROM game_users WHERE discord_user_id = ?", (user_id,)
        ).fetchone()["memilets"]
        if balance < price:
            raise PackError(f"{name}custa {price:,} Memilets.".replace(",", "."))
        if needs and not any(_free_count(connection, tier) for tier in needs):
            raise PackError(
                "Não há nenhum Ícone (ou melhor) livre no mercado agora, então a roleta não "
                "gira. Nada foi cobrado."
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
