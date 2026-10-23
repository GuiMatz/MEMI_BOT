"""Vender, doar e trocar jogadores, sempre de forma atômica.

- **Venda** (`sale_quote` / `sell_players`): a cotação mostra quanto cada jogador rende
  e o que se perde (vaga de titular, braçadeira, cobranças, bolas investidas) antes de
  confirmar. Vários jogadores podem ser vendidos de uma vez.
- **Doação** (`give_players`): jogadores e/ou Memilets vão direto para outra pessoa.
- **Troca** (`create_trade` / `settle_trade`): até `MAX_TRADE_PLAYERS` jogadores por
  lado, mais Memilets. Um lado pode ficar só com Memilets (compra) ou vazio (pedido de
  presente). Só a pessoa convidada aceita; quem propôs pode cancelar.

Ao sair de um elenco, o jogador deixa a escalação e perde capitania e cobranças.

Travas: jogador **protegido** (`toggle_protection`, o 🔒 do `mm!proteger`) não entra em
venda nenhuma. Jogador que veio de **pacote** pode ser vendido na hora, mas por
`PACK_SALE_FACTOR` do valor (a marca acompanha a carta mesmo depois de trocas), e passa
`PACK_LOCK` sem poder ser doado ou trocado.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from src.db import DATABASE_PATH, connect
from src.game import register_user
from src.clock import brasilia_time
from src.perks import load_perks
from src.ratings import EFFECTIVE_OVERALL_SQL, GAME_OVERALL_SQL, RARITY_TIERS

TRADE_LIFETIME = timedelta(minutes=15)
MAX_TRADE_PLAYERS = 5
MAX_SALE_PLAYERS = 200
PACK_SALE_FACTOR = 0.5
PACK_LOCK = timedelta(hours=1)
# Valor da carta pelo overall de jogo (o da carta, sem bolas): 400 Memilets no 60 e
# +11,6% por ponto, dobrando a cada ~6 pontos (70 ≈ 1.200, 80 ≈ 3.600, 90 ≈ 10.800,
# 97 ≈ 23.300). Carta melhor sempre vale mais; o valor de mercado real não conta.
CARD_VALUE_AT_60 = 400
CARD_VALUE_GROWTH = 1.116
CARD_VALUE_MIN = 25


class TransactionError(Exception):
    """Erro de validação de venda, doação ou troca."""


def sale_value(game_overall: int, multiplier: float = 1.0) -> int:
    """Memilets que a carta rende na venda (curva pelo overall × upgrade de venda)."""
    if not 1 <= game_overall <= 100:
        raise TransactionError("Overall inválido para calcular o valor da carta.")
    value = CARD_VALUE_AT_60 * CARD_VALUE_GROWTH ** (game_overall - 60) * multiplier
    return max(CARD_VALUE_MIN, round(value))


def market_values(
    player_ids: Iterable[int], database_path: str | Path = DATABASE_PATH
) -> dict[int, int]:
    """Valor de venda de cada jogador, sem upgrades (referência para trocas)."""
    ids = list(dict.fromkeys(int(player_id) for player_id in player_ids))
    if not ids:
        return {}
    marks = ", ".join("?" for _ in ids)
    with connect(database_path) as connection:
        rows = connection.execute(
            f"SELECT p.id, {GAME_OVERALL_SQL} AS game_overall FROM players AS p "
            f"WHERE p.id IN ({marks})",
            ids,
        ).fetchall()
    return {int(row["id"]): sale_value(int(row["game_overall"])) for row in rows}


def _owned_players(
    connection: sqlite3.Connection, owner_id: str, player_ids: Iterable[int]
) -> list[dict[str, Any]]:
    """Os jogadores pedidos, com o que importa para a venda; erro se algum não é do dono."""
    ids = list(dict.fromkeys(int(player_id) for player_id in player_ids))
    if not ids:
        return []
    marks = ", ".join("?" for _ in ids)
    rows = connection.execute(
        f"""
        SELECT p.id, COALESCE(NULLIF(p.display_name, ''), p.name) AS name, p.game_position,
               p.club_name, {GAME_OVERALL_SQL} AS game_overall,
               {EFFECTIVE_OVERALL_SQL} AS overall, COALESCE(b.balls, 0) AS balls,
               l.slot_order IS NOT NULL AS starter,
               x.captain_player_id = p.id AS captain,
               x.free_kick_player_id = p.id AS free_kick_taker,
               x.penalty_player_id = p.id AS penalty_taker,
               o.locked, o.pack_origin, o.pack_locked_until
        FROM player_ownership AS o
        JOIN players AS p ON p.id = o.player_id
        LEFT JOIN player_balls AS b ON b.player_id = p.id
        LEFT JOIN team_lineups AS l
          ON l.player_id = p.id AND l.discord_user_id = o.discord_user_id
        LEFT JOIN team_tactics AS x ON x.discord_user_id = o.discord_user_id
        WHERE o.discord_user_id = ? AND p.id IN ({marks})
        """,
        (owner_id, *ids),
    ).fetchall()
    found = {int(row["id"]): dict(row) for row in rows}
    if len(found) != len(ids):
        raise TransactionError("Algum desses jogadores não está mais no elenco.")
    return [found[player_id] for player_id in ids]


def _player_sale_value(player: dict[str, Any], multiplier: float = 1.0) -> int:
    factor = PACK_SALE_FACTOR if player.get("pack_origin") else 1.0
    return sale_value(int(player["game_overall"]), multiplier * factor)


def _pack_locked(player: dict[str, Any], now: datetime) -> datetime | None:
    until = player.get("pack_locked_until")
    if not until:
        return None
    moment = datetime.fromisoformat(until)
    return moment if moment > now else None


def _check_movable(players: Iterable[dict[str, Any]], action: str, *, selling=False) -> None:
    """Barra protegidos (só na venda) e, na doação e na troca, cartas de pacote travadas.

    Vender carta de pacote é livre a qualquer hora (pelo valor reduzido).
    """
    now = _utc_now()
    for player in players:
        if selling and player.get("locked"):
            raise TransactionError(
                f"🔒 **{player['name']}** está protegido. Tire a proteção com "
                f"`mm!proteger {player['name']}` antes de vender."
            )
        until = None if selling else _pack_locked(player, now)
        if until is not None:
            raise TransactionError(
                f"📦 **{player['name']}** veio de pacote e só pode ser {action} a partir de "
                f"**{brasilia_time(until)}**."
            )


def player_warnings(player: dict[str, Any], *, selling: bool = True) -> list[str]:
    """O que muda no time quando o jogador sai."""
    warnings = []
    if player.get("starter"):
        warnings.append("é titular e sai da escalação")
    roles = [
        label
        for key, label in (
            ("captain", "capitão"),
            ("free_kick_taker", "cobrador de falta"),
            ("penalty_taker", "cobrador de pênalti"),
        )
        if player.get(key)
    ]
    if roles:
        warnings.append("deixa de ser " + " e ".join(roles))
    if selling and player.get("balls"):
        balls = int(player["balls"])
        warnings.append(f"as {balls} bola{'s' if balls > 1 else ''} investidas se perdem")
    if selling and player.get("pack_origin"):
        warnings.append(f"veio de pacote: vende por {round(PACK_SALE_FACTOR * 100)}% do valor")
    return warnings


def sale_quote(
    discord_user_id: int | str,
    player_ids: Iterable[int],
    database_path: str | Path = DATABASE_PATH,
) -> dict[str, Any]:
    """Prévia da venda: valor de cada jogador, total, saldo antes e depois e avisos."""
    user_id = str(discord_user_id)
    with connect(database_path) as connection:
        players = _owned_players(connection, user_id, player_ids)
        if not players:
            raise TransactionError("Escolha pelo menos um jogador para vender.")
        _check_movable(players, "vendido", selling=True)
        multiplier = load_perks(connection, user_id).dismiss_refund_multiplier
        balance = connection.execute(
            "SELECT memilets FROM game_users WHERE discord_user_id = ?", (user_id,)
        ).fetchone()["memilets"]
    for player in players:
        player["value"] = _player_sale_value(player, multiplier)
        player["warnings"] = player_warnings(player)
    total = sum(player["value"] for player in players)
    return {
        "players": players,
        "total": total,
        "multiplier": multiplier,
        "balance": balance,
        "balance_after": balance + total,
    }


def _leave_team(connection: sqlite3.Connection, owner_id: str, player_id: int) -> None:
    """Tira o jogador da escalação e das funções do time de quem o tinha."""
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


def _move_player(connection, player_id: int, from_id: str, to_id: str) -> None:
    _leave_team(connection, from_id, player_id)
    connection.execute(
        "UPDATE player_ownership SET discord_user_id = ? WHERE player_id = ? "
        "AND discord_user_id = ?",
        (to_id, player_id, from_id),
    )


def _add_memilets(connection, user_id: str, amount: int) -> None:
    connection.execute(
        "UPDATE game_users SET memilets = memilets + ?, updated_at = CURRENT_TIMESTAMP "
        "WHERE discord_user_id = ?",
        (amount, user_id),
    )


def _require_balance(connection, user_id: str, amount: int, message: str) -> None:
    balance = connection.execute(
        "SELECT memilets FROM game_users WHERE discord_user_id = ?", (user_id,)
    ).fetchone()
    if balance is None or balance["memilets"] < amount:
        raise TransactionError(message)


def sell_players(
    discord_user_id: int | str,
    player_ids: Iterable[int],
    database_path: str | Path = DATABASE_PATH,
) -> int:
    """Vende os jogadores (todos ou nenhum) e devolve o total recebido."""
    user_id = str(discord_user_id)
    ids = list(player_ids)
    if len(ids) > MAX_SALE_PLAYERS:
        raise TransactionError(f"Venda no máximo {MAX_SALE_PLAYERS} jogadores por vez.")
    with connect(database_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        players = _owned_players(connection, user_id, ids)
        if not players:
            raise TransactionError("Escolha pelo menos um jogador para vender.")
        _check_movable(players, "vendido", selling=True)
        multiplier = load_perks(connection, user_id).dismiss_refund_multiplier
        total = 0
        for player in players:
            total += _player_sale_value(player, multiplier)
            _leave_team(connection, user_id, int(player["id"]))
            connection.execute("DELETE FROM player_balls WHERE player_id = ?", (player["id"],))
            connection.execute(
                "DELETE FROM player_ownership WHERE discord_user_id = ? AND player_id = ?",
                (user_id, player["id"]),
            )
        _add_memilets(connection, user_id, total)
        return total


def dismiss_player(
    discord_user_id: int | str,
    player_id: int,
    database_path: str | Path = DATABASE_PATH,
) -> int:
    """Vende um jogador (nome antigo de `sell_players`)."""
    return sell_players(discord_user_id, [player_id], database_path)


def give_players(
    giver_id: int | str,
    giver_name: str,
    recipient_id: int | str,
    recipient_name: str,
    player_ids: Iterable[int] = (),
    memilets: int = 0,
    database_path: str | Path = DATABASE_PATH,
) -> list[dict[str, Any]]:
    """Doa jogadores e/ou Memilets; devolve os jogadores doados."""
    giver, recipient = str(giver_id), str(recipient_id)
    ids = list(player_ids)
    if giver == recipient:
        raise TransactionError("Você não pode doar para você mesmo.")
    if memilets < 0:
        raise TransactionError("O valor de Memilets não pode ser negativo.")
    if not ids and not memilets:
        raise TransactionError("Escolha um jogador ou um valor para doar.")
    register_user(giver, giver_name, database_path)
    register_user(recipient, recipient_name, database_path)
    with connect(database_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        players = _owned_players(connection, giver, ids)
        _check_movable(players, "doado")
        _require_balance(connection, giver, memilets, "Você não tem Memilets suficientes.")
        for player in players:
            _move_player(connection, int(player["id"]), giver, recipient)
        _add_memilets(connection, giver, -memilets)
        _add_memilets(connection, recipient, memilets)
    return players


def create_trade(
    proposer_id: int | str,
    proposer_name: str,
    recipient_id: int | str,
    recipient_name: str,
    proposer_player_ids: Iterable[int],
    recipient_player_ids: Iterable[int],
    proposer_memilets: int = 0,
    recipient_memilets: int = 0,
    database_path: str | Path = DATABASE_PATH,
    *,
    now: datetime | None = None,
) -> int:
    """Registra a proposta (já confirmada por quem propôs) e devolve o número dela."""
    if proposer_memilets < 0 or recipient_memilets < 0:
        raise TransactionError("Os valores de Memilets não podem ser negativos.")
    proposer, recipient = str(proposer_id), str(recipient_id)
    if proposer == recipient:
        raise TransactionError("Você não pode trocar com você mesmo.")
    offered = list(dict.fromkeys(int(value) for value in proposer_player_ids))
    requested = list(dict.fromkeys(int(value) for value in recipient_player_ids))
    if max(len(offered), len(requested)) > MAX_TRADE_PLAYERS:
        raise TransactionError(f"No máximo {MAX_TRADE_PLAYERS} jogadores de cada lado.")
    if not (offered or requested or proposer_memilets or recipient_memilets):
        raise TransactionError("A troca está vazia: escolha jogadores ou Memilets.")
    register_user(proposer, proposer_name, database_path)
    register_user(recipient, recipient_name, database_path)
    current_time = _utc_now(now)
    with connect(database_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        _check_movable(_owned_players(connection, proposer, offered), "trocado")
        _check_movable(_owned_players(connection, recipient, requested), "trocado")
        _require_balance(
            connection, proposer, proposer_memilets, "Você não tem Memilets suficientes."
        )
        _require_balance(
            connection,
            recipient,
            recipient_memilets,
            f"{recipient_name} não tem os Memilets pedidos.",
        )
        trade_id = connection.execute(
            """
            INSERT INTO trades (
                proposer_id, recipient_id, proposer_memilets, recipient_memilets,
                created_at, expires_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                proposer,
                recipient,
                proposer_memilets,
                recipient_memilets,
                current_time.isoformat(timespec="seconds"),
                (current_time + TRADE_LIFETIME).isoformat(timespec="seconds"),
            ),
        ).lastrowid
        connection.executemany(
            "INSERT INTO trade_items (trade_id, side, player_id) VALUES (?, ?, ?)",
            [(trade_id, "proposer", player_id) for player_id in offered]
            + [(trade_id, "recipient", player_id) for player_id in requested],
        )
        return int(trade_id)


def trade_details(
    trade_id: int, database_path: str | Path = DATABASE_PATH
) -> dict[str, Any] | None:
    """A proposta com os jogadores de cada lado (overall, posição, valor de venda)."""
    with connect(database_path) as connection:
        trade = connection.execute("SELECT * FROM trades WHERE id = ?", (trade_id,)).fetchone()
        if trade is None:
            return None
        result = dict(trade)
        for side in ("proposer", "recipient"):
            rows = connection.execute(
                f"""
                SELECT p.id, COALESCE(NULLIF(p.display_name, ''), p.name) AS name,
                       p.game_position, {GAME_OVERALL_SQL} AS game_overall,
                       {EFFECTIVE_OVERALL_SQL} AS overall
                FROM trade_items AS i
                JOIN players AS p ON p.id = i.player_id
                LEFT JOIN player_balls AS b ON b.player_id = p.id
                WHERE i.trade_id = ? AND i.side = ?
                ORDER BY overall DESC, p.id
                """,
                (trade_id, side),
            ).fetchall()
            players = [dict(row) for row in rows]
            for player in players:
                player["value"] = sale_value(int(player["game_overall"]))
            result[f"{side}_players"] = players
    return result


def settle_trade(
    trade_id: int,
    discord_user_id: int | str,
    accept: bool,
    database_path: str | Path = DATABASE_PATH,
    *,
    now: datetime | None = None,
) -> str:
    """Aceita ou recusa (só a pessoa convidada). Devolve accepted/declined/expired."""
    user_id = str(discord_user_id)
    current_time = _utc_now(now)
    with connect(database_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        trade = _pending_trade(connection, trade_id)
        if trade["recipient_id"] != user_id:
            raise TransactionError("Somente a pessoa convidada pode responder à troca.")
        if current_time >= datetime.fromisoformat(trade["expires_at"]):
            _set_status(connection, trade_id, "expired")
            return "expired"
        if not accept:
            _set_status(connection, trade_id, "declined")
            return "declined"
        sides = {
            side: [
                int(row["player_id"])
                for row in connection.execute(
                    "SELECT player_id FROM trade_items WHERE trade_id = ? AND side = ?",
                    (trade_id, side),
                )
            ]
            for side in ("proposer", "recipient")
        }
        proposer, recipient = trade["proposer_id"], trade["recipient_id"]
        try:
            moving = _owned_players(connection, proposer, sides["proposer"])
            moving += _owned_players(connection, recipient, sides["recipient"])
        except TransactionError as error:
            raise TransactionError(
                "Um dos jogadores mudou de dono desde a proposta; a troca não foi feita."
            ) from error
        _check_movable(moving, "trocado")
        for owner, amount in (
            (proposer, trade["proposer_memilets"]),
            (recipient, trade["recipient_memilets"]),
        ):
            _require_balance(
                connection,
                owner,
                amount,
                "Uma das contas já não tem saldo suficiente; a troca não foi feita.",
            )
        for player_id in sides["proposer"]:
            _move_player(connection, player_id, proposer, recipient)
        for player_id in sides["recipient"]:
            _move_player(connection, player_id, recipient, proposer)
        balance = trade["recipient_memilets"] - trade["proposer_memilets"]
        _add_memilets(connection, proposer, balance)
        _add_memilets(connection, recipient, -balance)
        _set_status(connection, trade_id, "accepted")
        return "accepted"


def toggle_protection(
    discord_user_id: int | str, player_id: int, database_path: str | Path = DATABASE_PATH
) -> bool:
    """Liga ou desliga o 🔒 do jogador; devolve se ficou protegido."""
    with connect(database_path) as connection:
        row = connection.execute(
            "SELECT locked FROM player_ownership WHERE discord_user_id = ? AND player_id = ?",
            (str(discord_user_id), player_id),
        ).fetchone()
        if row is None:
            raise TransactionError("Esse jogador não está no seu elenco.")
        locked = not row["locked"]
        connection.execute(
            "UPDATE player_ownership SET locked = ? WHERE player_id = ?", (int(locked), player_id)
        )
    return locked


def set_protection(
    discord_user_id: int | str,
    player_ids: Iterable[int],
    locked: bool,
    database_path: str | Path = DATABASE_PATH,
) -> int:
    """Põe ou tira o 🔒 de vários jogadores do dono; devolve quantos mudaram."""
    ids = list(dict.fromkeys(int(player_id) for player_id in player_ids))
    if not ids:
        return 0
    marks = ", ".join("?" for _ in ids)
    with connect(database_path) as connection:
        cursor = connection.execute(
            f"UPDATE player_ownership SET locked = ? WHERE discord_user_id = ? "
            f"AND player_id IN ({marks}) AND locked != ?",
            (int(locked), str(discord_user_id), *ids, int(locked)),
        )
    return cursor.rowcount


def quick_sale_ids(
    discord_user_id: int | str,
    database_path: str | Path = DATABASE_PATH,
    *,
    rarity: str | None = None,
    below: int | None = None,
    position: str | None = None,
) -> list[int]:
    """Reservas que a venda rápida pode vender com o filtro (sem 🔒)."""
    clauses = [
        "o.discord_user_id = ?",
        "o.locked = 0",
        "NOT EXISTS (SELECT 1 FROM team_lineups AS l "
        "WHERE l.player_id = p.id AND l.discord_user_id = o.discord_user_id)",
    ]
    parameters: list[Any] = [str(discord_user_id)]
    if rarity is not None:
        index = next(
            (i for i, tier in enumerate(RARITY_TIERS) if tier[0].casefold() == rarity.casefold()),
            None,
        )
        if index is None:
            raise TransactionError("Raridade desconhecida.")
        clauses.append(f"rarity_index({EFFECTIVE_OVERALL_SQL}) = ?")
        parameters.append(index)
    if below is not None:
        clauses.append(f"{EFFECTIVE_OVERALL_SQL} < ?")
        parameters.append(below)
    if position is not None:
        clauses.append("p.game_position = ?")
        parameters.append(position)
    with connect(database_path) as connection:
        rows = connection.execute(
            f"""
            SELECT p.id FROM player_ownership AS o
            JOIN players AS p ON p.id = o.player_id
            LEFT JOIN player_balls AS b ON b.player_id = p.id
            WHERE {' AND '.join(clauses)}
            ORDER BY {EFFECTIVE_OVERALL_SQL}, p.id
            LIMIT {MAX_SALE_PLAYERS}
            """,
            parameters,
        ).fetchall()
    return [int(row["id"]) for row in rows]


def cancel_trade(
    trade_id: int, discord_user_id: int | str, database_path: str | Path = DATABASE_PATH
) -> None:
    """Quem propôs desiste da proposta enquanto ela não foi respondida."""
    with connect(database_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        trade = _pending_trade(connection, trade_id)
        if trade["proposer_id"] != str(discord_user_id):
            raise TransactionError("Só quem fez a proposta pode cancelá-la.")
        _set_status(connection, trade_id, "cancelled")


def _pending_trade(connection, trade_id: int) -> sqlite3.Row:
    trade = connection.execute("SELECT * FROM trades WHERE id = ?", (trade_id,)).fetchone()
    if trade is None:
        raise TransactionError("Essa proposta de troca não existe.")
    if trade["status"] != "pending":
        raise TransactionError("Essa proposta já foi respondida, cancelada ou expirou.")
    return trade


def _set_status(connection, trade_id: int, status: str) -> None:
    connection.execute("UPDATE trades SET status = ? WHERE id = ?", (status, trade_id))


def _utc_now(now: datetime | None = None) -> datetime:
    current_time = now or datetime.now(timezone.utc)
    if current_time.tzinfo is None or current_time.utcoffset() is None:
        raise ValueError("now deve incluir um fuso horário.")
    return current_time.astimezone(timezone.utc)
