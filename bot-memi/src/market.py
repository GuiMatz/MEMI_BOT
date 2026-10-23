"""Mercado de jogadores entre pessoas (`mm!mercado`, `mm!anunciar`, `mm!anuncios`).

Quem anuncia escolhe o preço; a carta continua no elenco até alguém comprar. Na compra,
a carta muda de dono (sai da escalação e das funções de quem vendeu), quem compra paga o
preço e quem vende recebe o preço menos a taxa (`MARKET_FEE`), que sai de circulação.

Anúncio vale `LISTING_DAYS` dias, cada pessoa tem até `MAX_LISTINGS` ativos e um jogador
só pode estar em um anúncio por vez. Cartas protegidas (🔒) ou ainda na trava do pacote
não podem ser anunciadas nem compradas. Se a carta sair do elenco de outro jeito (venda,
doação, troca), o anúncio some sozinho.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from src.db import DATABASE_PATH, connect
from src.game import register_user
from src.ratings import EFFECTIVE_OVERALL_SQL
from src.transactions import (
    TransactionError,
    _add_memilets,
    _check_movable,
    _move_player,
    _owned_players,
    _utc_now,
)

MARKET_FEE = 0.05
LISTING_DAYS = 3
MAX_LISTINGS = 10
MIN_PRICE = 100
MAX_PRICE = 10_000_000
SORTS = {
    "novos": "l.created_at DESC, l.id DESC",
    "baratos": "l.price ASC, l.id DESC",
    "melhores": "overall DESC, l.price ASC",
}


class MarketError(Exception):
    """Anúncio ou compra recusada; a mensagem vai para a pessoa."""


@dataclass(frozen=True)
class Purchase:
    listing_id: int
    player_name: str
    price: int
    fee: int
    seller_id: str


def fee_for(price: int) -> int:
    return round(price * MARKET_FEE)


def _iso(moment: datetime) -> str:
    return moment.isoformat(timespec="seconds")


def create_listing(
    seller_id: int | str,
    seller_name: str,
    player_id: int,
    price: int,
    database_path: str | Path = DATABASE_PATH,
    now: datetime | None = None,
) -> int:
    """Anuncia um jogador do elenco; devolve o número do anúncio."""
    if not MIN_PRICE <= price <= MAX_PRICE:
        raise MarketError(
            f"O preço vai de {MIN_PRICE:,} a {MAX_PRICE:,} Memilets.".replace(",", ".")
        )
    seller = str(seller_id)
    current = _utc_now(now)
    register_user(seller, seller_name, database_path)
    with connect(database_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        try:
            (player,) = _owned_players(connection, seller, [player_id])
            if player.get("locked"):
                raise MarketError(
                    f"🔒 **{player['name']}** está protegido. Tire a proteção antes de anunciar."
                )
            _check_movable([player], "anunciado")
        except TransactionError as error:
            raise MarketError(str(error)) from None
        active = connection.execute(
            "SELECT COUNT(*) FROM market_listings WHERE seller_id = ? AND status = 'active' "
            "AND expires_at > ?",
            (seller, _iso(current)),
        ).fetchone()[0]
        if active >= MAX_LISTINGS:
            raise MarketError(f"Você já tem {MAX_LISTINGS} anúncios ativos. Cancele algum antes.")
        # Anúncio vencido do mesmo jogador não conta: libera para anunciar de novo.
        connection.execute(
            "UPDATE market_listings SET status = 'expired' WHERE player_id = ? "
            "AND status = 'active' AND expires_at <= ?",
            (player_id, _iso(current)),
        )
        if connection.execute(
            "SELECT 1 FROM market_listings WHERE player_id = ? AND status = 'active'",
            (player_id,),
        ).fetchone():
            raise MarketError(f"**{player['name']}** já está anunciado.")
        cursor = connection.execute(
            "INSERT INTO market_listings (seller_id, player_id, price, created_at, expires_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (seller, player_id, price, _iso(current), _iso(current + timedelta(days=LISTING_DAYS))),
        )
        return int(cursor.lastrowid)


def list_listings(
    database_path: str | Path = DATABASE_PATH,
    *,
    sort: str = "novos",
    seller_id: int | str | None = None,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Anúncios valendo (a carta ainda é de quem anunciou), na ordem pedida."""
    where = ["l.status = 'active'", "l.expires_at > ?"]
    arguments: list[Any] = [_iso(_utc_now(now))]
    if seller_id is not None:
        where.append("l.seller_id = ?")
        arguments.append(str(seller_id))
    with connect(database_path) as connection:
        rows = connection.execute(
            f"""
            SELECT l.id, l.seller_id, l.price, l.expires_at, p.id AS player_id,
                   COALESCE(NULLIF(p.display_name, ''), p.name) AS name, p.game_position,
                   {EFFECTIVE_OVERALL_SQL} AS overall, u.display_name AS seller_name
            FROM market_listings AS l
            JOIN player_ownership AS o
              ON o.player_id = l.player_id AND o.discord_user_id = l.seller_id
            JOIN players AS p ON p.id = l.player_id
            LEFT JOIN player_balls AS b ON b.player_id = p.id
            LEFT JOIN game_users AS u ON u.discord_user_id = l.seller_id
            WHERE {" AND ".join(where)}
            ORDER BY {SORTS.get(sort, SORTS["novos"])}
            """,
            arguments,
        ).fetchall()
    return [dict(row) for row in rows]


def buy_listing(
    buyer_id: int | str,
    buyer_name: str,
    listing_id: int,
    database_path: str | Path = DATABASE_PATH,
    now: datetime | None = None,
) -> Purchase:
    """Compra o anúncio: a carta muda de dono e o dinheiro (menos a taxa) vai para quem vendeu."""
    buyer = str(buyer_id)
    current = _utc_now(now)
    register_user(buyer, buyer_name, database_path)
    with connect(database_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        listing = connection.execute(
            "SELECT * FROM market_listings WHERE id = ?", (listing_id,)
        ).fetchone()
        if (
            listing is None
            or listing["status"] != "active"
            or listing["expires_at"] <= _iso(current)
        ):
            raise MarketError("Esse anúncio não está mais disponível.")
        seller = str(listing["seller_id"])
        if seller == buyer:
            raise MarketError("Esse anúncio é seu. Para tirar do mercado, use `mm!anuncios`.")
        try:
            (player,) = _owned_players(connection, seller, [int(listing["player_id"])])
            if player.get("locked"):
                raise TransactionError("Quem anunciou protegeu esse jogador.")
            _check_movable([player], "vendido")
        except TransactionError as error:
            connection.execute(
                "UPDATE market_listings SET status = 'cancelled' WHERE id = ?", (listing_id,)
            )
            connection.commit()
            raise MarketError(f"Esse anúncio caiu: {error}") from None
        price = int(listing["price"])
        balance = connection.execute(
            "SELECT memilets FROM game_users WHERE discord_user_id = ?", (buyer,)
        ).fetchone()["memilets"]
        if balance < price:
            raise MarketError(
                f"Você precisa de {price:,} Memilets (tem {balance:,}).".replace(",", ".")
            )
        fee = fee_for(price)
        _move_player(connection, int(listing["player_id"]), seller, buyer)
        _add_memilets(connection, buyer, -price)
        _add_memilets(connection, seller, price - fee)
        connection.execute(
            "UPDATE market_listings SET status = 'sold', buyer_id = ?, sold_at = ? WHERE id = ?",
            (buyer, _iso(current), listing_id),
        )
    return Purchase(listing_id, player["name"], price, fee, seller)


def cancel_listing(
    seller_id: int | str, listing_id: int, database_path: str | Path = DATABASE_PATH
) -> None:
    with connect(database_path) as connection:
        cursor = connection.execute(
            "UPDATE market_listings SET status = 'cancelled' WHERE id = ? AND seller_id = ? "
            "AND status = 'active'",
            (listing_id, str(seller_id)),
        )
    if not cursor.rowcount:
        raise MarketError("Esse anúncio não é seu ou já saiu do mercado.")
