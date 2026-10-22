"""Demitir jogadores e concluir trocas de forma atômica."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.db import DATABASE_PATH, connect
from src.game import register_user
from src.perks import load_perks

TRADE_LIFETIME = timedelta(minutes=15)


class TransactionError(Exception):
    """Erro de validação de demissão ou troca."""


def player_market_value(overall: int, market_value_eur: int | None) -> int:
    if market_value_eur is not None and market_value_eur >= 0:
        return market_value_eur
    if not 1 <= overall <= 100:
        raise TransactionError("Overall inválido para calcular o valor de mercado.")
    return round(5_000 * 20_000 ** ((overall - 1) / 98))


def dismiss_player(
    discord_user_id: int | str,
    player_id: int,
    database_path: str | Path = DATABASE_PATH,
) -> int:
    user_id = str(discord_user_id)
    with connect(database_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        player = connection.execute(
            """
            SELECT p.overall, p.market_value_eur
            FROM player_ownership AS o
            JOIN players AS p ON p.id = o.player_id
            WHERE o.discord_user_id = ? AND p.id = ?
            """,
            (user_id, player_id),
        ).fetchone()
        if player is None:
            raise TransactionError("Esse jogador não pertence ao seu elenco.")
        market_value = player_market_value(
            int(player["overall"]),
            player["market_value_eur"],
        )
        multiplier = load_perks(connection, user_id).dismiss_refund_multiplier
        refund = max(1, round(market_value * 0.0005 * multiplier))
        connection.execute(
            """
            DELETE FROM team_lineups
            WHERE discord_user_id = ? AND player_id = ?
            """,
            (user_id, player_id),
        )
        connection.execute(
            """
            UPDATE team_tactics SET
                captain_player_id = CASE WHEN captain_player_id = ? THEN NULL ELSE captain_player_id END,
                free_kick_player_id = CASE WHEN free_kick_player_id = ? THEN NULL ELSE free_kick_player_id END,
                penalty_player_id = CASE WHEN penalty_player_id = ? THEN NULL ELSE penalty_player_id END
            WHERE discord_user_id = ?
            """,
            (player_id, player_id, player_id, user_id),
        )
        connection.execute(
            "DELETE FROM player_balls WHERE player_id = ?",
            (player_id,),
        )
        connection.execute(
            """
            DELETE FROM player_ownership
            WHERE discord_user_id = ? AND player_id = ?
            """,
            (user_id, player_id),
        )
        connection.execute(
            """
            UPDATE game_users
            SET memilets = memilets + ?, updated_at = CURRENT_TIMESTAMP
            WHERE discord_user_id = ?
            """,
            (refund, user_id),
        )
        return refund


def create_trade(
    proposer_id: int | str,
    proposer_name: str,
    recipient_id: int | str,
    recipient_name: str,
    proposer_player_id: int,
    recipient_player_id: int,
    proposer_memilets: int,
    recipient_memilets: int,
    database_path: str | Path = DATABASE_PATH,
    *,
    now: datetime | None = None,
) -> int:
    if proposer_memilets < 0 or recipient_memilets < 0:
        raise TransactionError("Os valores de Memilets não podem ser negativos.")
    proposer = str(proposer_id)
    recipient = str(recipient_id)
    if proposer == recipient:
        raise TransactionError("Você não pode trocar com você mesmo.")
    register_user(proposer, proposer_name, database_path)
    register_user(recipient, recipient_name, database_path)
    current_time = _utc_now(now)
    with connect(database_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        for user_id, player_id in (
            (proposer, proposer_player_id),
            (recipient, recipient_player_id),
        ):
            owner = connection.execute(
                """
                SELECT 1 FROM player_ownership
                WHERE discord_user_id = ? AND player_id = ?
                """,
                (user_id, player_id),
            ).fetchone()
            if owner is None:
                raise TransactionError("Cada lado precisa oferecer um jogador do próprio elenco.")
        for user_id, amount in (
            (proposer, proposer_memilets),
            (recipient, recipient_memilets),
        ):
            balance = connection.execute(
                "SELECT memilets FROM game_users WHERE discord_user_id = ?",
                (user_id,),
            ).fetchone()["memilets"]
            if balance < amount:
                raise TransactionError(
                    f"A conta {user_id} não tem Memilets suficientes para a oferta."
                )
        cursor = connection.execute(
            """
            INSERT INTO trade_offers (
                proposer_id, recipient_id, proposer_player_id, recipient_player_id,
                proposer_memilets, recipient_memilets, proposer_confirmed,
                recipient_confirmed, created_at, expires_at
            ) VALUES (?, ?, ?, ?, ?, ?, 1, 0, ?, ?)
            """,
            (
                proposer,
                recipient,
                proposer_player_id,
                recipient_player_id,
                proposer_memilets,
                recipient_memilets,
                current_time.isoformat(timespec="seconds"),
                (current_time + TRADE_LIFETIME).isoformat(timespec="seconds"),
            ),
        )
        return int(cursor.lastrowid)


def settle_trade(
    offer_id: int,
    discord_user_id: int | str,
    accept: bool,
    database_path: str | Path = DATABASE_PATH,
    *,
    now: datetime | None = None,
) -> str:
    user_id = str(discord_user_id)
    current_time = _utc_now(now)
    with connect(database_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        offer = connection.execute(
            "SELECT * FROM trade_offers WHERE id = ?",
            (offer_id,),
        ).fetchone()
        if offer is None:
            raise TransactionError("A oferta de troca não existe.")
        if offer["recipient_id"] != user_id:
            raise TransactionError("Somente a pessoa convidada pode responder à troca.")
        if offer["status"] != "pending":
            raise TransactionError("Essa oferta já foi respondida ou expirou.")
        if current_time >= datetime.fromisoformat(offer["expires_at"]):
            connection.execute(
                "UPDATE trade_offers SET status = 'expired' WHERE id = ?",
                (offer_id,),
            )
            return "expired"
        if not accept:
            connection.execute(
                "UPDATE trade_offers SET status = 'declined' WHERE id = ?",
                (offer_id,),
            )
            return "declined"

        connection.execute(
            "UPDATE trade_offers SET recipient_confirmed = 1 WHERE id = ?",
            (offer_id,),
        )
        for owner_id, player_id in (
            (offer["proposer_id"], offer["proposer_player_id"]),
            (offer["recipient_id"], offer["recipient_player_id"]),
        ):
            owner = connection.execute(
                """
                SELECT 1 FROM player_ownership
                WHERE discord_user_id = ? AND player_id = ?
                """,
                (owner_id, player_id),
            ).fetchone()
            if owner is None:
                raise TransactionError("Um dos jogadores não pertence mais ao ofertante.")
        for owner_id, amount in (
            (offer["proposer_id"], offer["proposer_memilets"]),
            (offer["recipient_id"], offer["recipient_memilets"]),
        ):
            balance = connection.execute(
                "SELECT memilets FROM game_users WHERE discord_user_id = ?",
                (owner_id,),
            ).fetchone()["memilets"]
            if balance < amount:
                raise TransactionError(
                    "Uma das contas já não tem saldo suficiente; a troca não foi aplicada."
                )

        connection.execute(
            """
            UPDATE player_ownership SET discord_user_id = ?
            WHERE player_id = ? AND discord_user_id = ?
            """,
            (
                offer["recipient_id"],
                offer["proposer_player_id"],
                offer["proposer_id"],
            ),
        )
        connection.execute(
            """
            UPDATE player_ownership SET discord_user_id = ?
            WHERE player_id = ? AND discord_user_id = ?
            """,
            (
                offer["proposer_id"],
                offer["recipient_player_id"],
                offer["recipient_id"],
            ),
        )
        connection.execute(
            """
            UPDATE game_users SET
                memilets = memilets - ? + ?, updated_at = CURRENT_TIMESTAMP
            WHERE discord_user_id = ?
            """,
            (
                offer["proposer_memilets"],
                offer["recipient_memilets"],
                offer["proposer_id"],
            ),
        )
        connection.execute(
            """
            UPDATE game_users SET
                memilets = memilets - ? + ?, updated_at = CURRENT_TIMESTAMP
            WHERE discord_user_id = ?
            """,
            (
                offer["recipient_memilets"],
                offer["proposer_memilets"],
                offer["recipient_id"],
            ),
        )
        for owner_id, player_id in (
            (offer["proposer_id"], offer["proposer_player_id"]),
            (offer["recipient_id"], offer["recipient_player_id"]),
        ):
            connection.execute(
                "DELETE FROM team_lineups WHERE discord_user_id = ? AND player_id = ?",
                (owner_id, player_id),
            )
            connection.execute(
                """
                UPDATE team_tactics SET
                    captain_player_id = CASE WHEN captain_player_id = ? THEN NULL ELSE captain_player_id END,
                    free_kick_player_id = CASE WHEN free_kick_player_id = ? THEN NULL ELSE free_kick_player_id END,
                    penalty_player_id = CASE WHEN penalty_player_id = ? THEN NULL ELSE penalty_player_id END
                WHERE discord_user_id = ?
                """,
                (player_id, player_id, player_id, owner_id),
            )
        connection.execute(
            "UPDATE trade_offers SET status = 'accepted' WHERE id = ?",
            (offer_id,),
        )
        return "accepted"


def _utc_now(now: datetime | None = None) -> datetime:
    current_time = now or datetime.now(timezone.utc)
    if current_time.tzinfo is None or current_time.utcoffset() is None:
        raise ValueError("now deve incluir um fuso horário.")
    return current_time.astimezone(timezone.utc)
