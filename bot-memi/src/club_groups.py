"""Grupo de clubes: várias contas do mesmo dono, cada uma com o seu clube.

Os clubes continuam separados (jogam entre si e no campeonato como qualquer outro); o
grupo só deixa o dono **gerenciar todos por uma conta**:

- **Convite com aceite**: o dono cria o grupo (`create_group`) e convida (`invite`); a
  conta só entra aceitando (`accept`). Qualquer conta pode sair quando quiser (`leave`).
- **Ordem**: 1 = Principal, 2 = Secundária, 3 = Terciária… (`set_order`).
- **Otimizar** (`optimize_plan` / `apply_plan`): junta os jogadores do grupo e monta o
  melhor XI da Principal na formação dela; com o que sobra, o da Secundária, e assim por
  diante. O mesmo cálculo do `mm!escalar` automático (`teams.best_lineup`). Só quem vira
  titular em outra conta muda de dono; o resto fica onde está.
- **Mover** um jogador entre contas (`move_player`) e **vender em massa** em várias
  contas (`group_sale_ids`, que não vende quem seria titular em outra conta).

Trava: transferências entre as contas param `LOCK_BEFORE_GAMES` antes do horário do
campeonato quando alguma conta do grupo joga naquele dia, e voltam depois dos jogos.
Cartas de pacote ainda travadas (`PACK_LOCK`) não saem da conta.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from src import league
from src.clock import BRASILIA
from src.db import DATABASE_PATH, connect
from src.positions import slot_rating
from src.ratings import EFFECTIVE_OVERALL_SQL, GAME_OVERALL_SQL
from src.teams import FORMATIONS, best_lineup, upgrade_slot
from src.transactions import _leave_team, quick_sale_ids

MAX_MEMBERS = 5
MAX_NAME = 32
LOCK_BEFORE_GAMES = timedelta(minutes=15)
DEFAULT_GAME_TIME = "19:00"
RANK_LABELS = {1: "Principal", 2: "Secundária", 3: "Terciária", 4: "Quaternária", 5: "Quinta"}
RANK_WORDS = {
    "principal": 1,
    "primeira": 1,
    "secundaria": 2,
    "secundária": 2,
    "segunda": 2,
    "terciaria": 3,
    "terciária": 3,
    "terceira": 3,
    "quaternaria": 4,
    "quaternária": 4,
    "quarta": 4,
    "quinta": 5,
}


class GroupError(Exception):
    """Regra do grupo que pode ser mostrada para a pessoa."""


def rank_label(rank: int) -> str:
    return RANK_LABELS.get(rank, f"Conta {rank}")


# Grupo e membros ---------------------------------------------------------------------------


def _group_row(connection: sqlite3.Connection, user_id: str, status: str = "member"):
    return connection.execute(
        """
        SELECT g.*, m.rank, m.status FROM club_group_members AS m
        JOIN club_groups AS g ON g.id = m.group_id
        WHERE m.discord_user_id = ? AND m.status = ?
        """,
        (user_id, status),
    ).fetchone()


def _members(connection: sqlite3.Connection, group_id: int) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in connection.execute(
            """
            SELECT m.discord_user_id, m.rank, u.display_name, u.memilets,
                   t.team_name, x.formation
            FROM club_group_members AS m
            LEFT JOIN game_users AS u ON u.discord_user_id = m.discord_user_id
            LEFT JOIN game_teams AS t ON t.discord_user_id = m.discord_user_id
            LEFT JOIN team_tactics AS x ON x.discord_user_id = m.discord_user_id
            WHERE m.group_id = ? AND m.status = 'member'
            ORDER BY m.rank
            """,
            (group_id,),
        )
    ]


def group_of(user_id: int | str, database_path: str | Path = DATABASE_PATH) -> dict | None:
    """O grupo da conta (com os membros em ordem), ou None."""
    with connect(database_path) as connection:
        row = _group_row(connection, str(user_id))
        if row is None:
            return None
        return {
            "id": row["id"],
            "name": row["name"],
            "owner_user_id": row["owner_user_id"],
            "members": _members(connection, row["id"]),
        }


def pending_invite(user_id: int | str, database_path: str | Path = DATABASE_PATH) -> dict | None:
    with connect(database_path) as connection:
        row = _group_row(connection, str(user_id), "invited")
    return dict(row) if row else None


def create_group(owner_id: int | str, name: str, database_path: str | Path = DATABASE_PATH) -> dict:
    owner = str(owner_id)
    name = " ".join(name.split())[:MAX_NAME]
    if not name:
        raise GroupError("Dê um nome ao grupo: `mm!grupo criar <nome>`.")
    with connect(database_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        if _group_row(connection, owner) is not None:
            raise GroupError("Essa conta já está num grupo. Saia dele antes de criar outro.")
        connection.execute(
            "DELETE FROM club_group_members WHERE discord_user_id = ? AND status = 'invited'",
            (owner,),
        )
        group_id = connection.execute(
            "INSERT INTO club_groups (name, owner_user_id) VALUES (?, ?)", (name, owner)
        ).lastrowid
        connection.execute(
            "INSERT INTO club_group_members (group_id, discord_user_id, rank) VALUES (?, ?, 1)",
            (group_id, owner),
        )
    return group_of(owner, database_path)


def _owned_group(connection: sqlite3.Connection, owner_id: str):
    row = _group_row(connection, owner_id)
    if row is None:
        raise GroupError("Você não tem grupo. Crie um com `mm!grupo criar <nome>`.")
    if row["owner_user_id"] != owner_id:
        raise GroupError("Só o dono do grupo pode fazer isso.")
    return row


def invite(
    owner_id: int | str, account_id: int | str, database_path: str | Path = DATABASE_PATH
) -> dict:
    owner, account = str(owner_id), str(account_id)
    if owner == account:
        raise GroupError("Essa conta já é a dona do grupo.")
    with connect(database_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        group = _owned_group(connection, owner)
        if (
            connection.execute(
                "SELECT 1 FROM game_users WHERE discord_user_id = ?", (account,)
            ).fetchone()
            is None
        ):
            raise GroupError("Essa conta ainda não joga o FUT.AE.")
        current = connection.execute(
            "SELECT group_id, status FROM club_group_members WHERE discord_user_id = ?",
            (account,),
        ).fetchone()
        if current is not None and current["status"] == "member":
            raise GroupError("Essa conta já está num grupo.")
        if current is not None and current["group_id"] != group["id"]:
            raise GroupError("Essa conta já tem um convite de outro grupo pendente.")
        if current is not None:
            raise GroupError("O convite para essa conta já foi feito; falta ela aceitar.")
        total = connection.execute(
            "SELECT COUNT(*) FROM club_group_members WHERE group_id = ?", (group["id"],)
        ).fetchone()[0]
        if total >= MAX_MEMBERS:
            raise GroupError(f"Um grupo tem no máximo {MAX_MEMBERS} contas.")
        connection.execute(
            "INSERT INTO club_group_members (group_id, discord_user_id, rank, status) "
            "VALUES (?, ?, ?, 'invited')",
            (group["id"], account, 99),
        )
    return dict(group)


def answer_invite(
    account_id: int | str, accept: bool, database_path: str | Path = DATABASE_PATH
) -> dict:
    """Aceita (entra no fim da ordem) ou recusa o convite pendente. Devolve o grupo."""
    account = str(account_id)
    with connect(database_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = _group_row(connection, account, "invited")
        if row is None:
            raise GroupError("Essa conta não tem convite de grupo pendente.")
        if not accept:
            connection.execute(
                "DELETE FROM club_group_members WHERE discord_user_id = ?", (account,)
            )
            return dict(row)
        last = connection.execute(
            "SELECT MAX(rank) FROM club_group_members WHERE group_id = ? AND status = 'member'",
            (row["id"],),
        ).fetchone()[0]
        connection.execute(
            "UPDATE club_group_members SET status = 'member', rank = ?, "
            "joined_at = CURRENT_TIMESTAMP WHERE discord_user_id = ?",
            ((last or 0) + 1, account),
        )
    return group_of(account, database_path)


def _renumber(connection: sqlite3.Connection, group_id: int) -> None:
    rows = connection.execute(
        "SELECT discord_user_id FROM club_group_members "
        "WHERE group_id = ? AND status = 'member' ORDER BY rank",
        (group_id,),
    ).fetchall()
    for rank, row in enumerate(rows, start=1):
        connection.execute(
            "UPDATE club_group_members SET rank = ? WHERE discord_user_id = ?",
            (rank, row["discord_user_id"]),
        )


def leave(account_id: int | str, database_path: str | Path = DATABASE_PATH) -> dict:
    """A conta sai do grupo (o dono desfaz o grupo com `disband`)."""
    account = str(account_id)
    with connect(database_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = _group_row(connection, account)
        if row is None:
            raise GroupError("Essa conta não está em grupo nenhum.")
        if row["owner_user_id"] == account:
            raise GroupError("Você é o dono: para acabar com o grupo, use `mm!grupo desfazer`.")
        connection.execute("DELETE FROM club_group_members WHERE discord_user_id = ?", (account,))
        _renumber(connection, row["id"])
    return dict(row)


def remove_member(
    owner_id: int | str, account_id: int | str, database_path: str | Path = DATABASE_PATH
) -> None:
    """O dono tira uma conta do grupo (ou cancela o convite dela)."""
    owner, account = str(owner_id), str(account_id)
    if owner == account:
        raise GroupError("Para acabar com o grupo, use `mm!grupo desfazer`.")
    with connect(database_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        group = _owned_group(connection, owner)
        removed = connection.execute(
            "DELETE FROM club_group_members WHERE group_id = ? AND discord_user_id = ?",
            (group["id"], account),
        ).rowcount
        if not removed:
            raise GroupError("Essa conta não está no seu grupo.")
        _renumber(connection, group["id"])


def disband(owner_id: int | str, database_path: str | Path = DATABASE_PATH) -> None:
    owner = str(owner_id)
    with connect(database_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        group = _owned_group(connection, owner)
        connection.execute("DELETE FROM club_group_members WHERE group_id = ?", (group["id"],))
        connection.execute("DELETE FROM club_groups WHERE id = ?", (group["id"],))


def rename(owner_id: int | str, name: str, database_path: str | Path = DATABASE_PATH) -> None:
    name = " ".join(name.split())[:MAX_NAME]
    if not name:
        raise GroupError("Escreva o nome novo: `mm!grupo nome <nome>`.")
    with connect(database_path) as connection:
        group = _owned_group(connection, str(owner_id))
        connection.execute("UPDATE club_groups SET name = ? WHERE id = ?", (name, group["id"]))


def set_order(
    owner_id: int | str, ordered: Iterable[int | str], database_path: str | Path = DATABASE_PATH
) -> dict:
    """As contas citadas vão para o topo, nessa ordem; as outras vêm depois, como estavam."""
    owner = str(owner_id)
    wanted = list(dict.fromkeys(str(user) for user in ordered))
    with connect(database_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        group = _owned_group(connection, owner)
        current = [m["discord_user_id"] for m in _members(connection, group["id"])]
        strangers = [user for user in wanted if user not in current]
        if strangers:
            raise GroupError("Só dá para ordenar contas que já estão no grupo.")
        order = wanted + [user for user in current if user not in wanted]
        for rank, user in enumerate(order, start=1):
            connection.execute(
                "UPDATE club_group_members SET rank = ? WHERE discord_user_id = ?", (rank, user)
            )
    return group_of(owner, database_path)


def managed_account(
    manager_id: int | str, account: int | str, database_path: str | Path = DATABASE_PATH
) -> dict:
    """A conta do grupo que `manager_id` pode gerenciar: a própria ou, para o dono, qualquer
    uma. `account` é o ID da conta ou a posição na ordem (1, 2, 3…)."""
    manager = str(manager_id)
    group = group_of(manager, database_path)
    if group is None:
        raise GroupError("Você não está em grupo nenhum. Crie um com `mm!grupo criar <nome>`.")
    text = str(account)
    by_rank = {str(m["rank"]): m for m in group["members"]}
    by_id = {m["discord_user_id"]: m for m in group["members"]}
    member = by_id.get(text) or (by_rank.get(text) if len(text) <= 2 else None)
    if member is None:
        raise GroupError("Essa conta não está no seu grupo.")
    if member["discord_user_id"] != manager and group["owner_user_id"] != manager:
        raise GroupError("Só o dono do grupo gerencia as outras contas.")
    return member


def parse_rank(word: str) -> int | None:
    """`2`, `secundaria`, `principal`… → posição na ordem; None se não é isso."""
    text = word.strip().casefold()
    if text.isdigit() and 1 <= int(text) <= MAX_MEMBERS:
        return int(text)
    return RANK_WORDS.get(text)


# Trava antes dos jogos do campeonato ---------------------------------------------------------


def transfer_lock(
    user_ids: Iterable[int | str],
    database_path: str | Path = DATABASE_PATH,
    *,
    now: datetime | None = None,
) -> str | None:
    """Motivo da trava, se alguma das contas joga o campeonato agora; None se está livre.

    Trava de `LOCK_BEFORE_GAMES` antes do horário do servidor até os jogos do dia saírem
    (servidor pausado ou com o agendador desligado não trava)."""
    users = {str(user) for user in user_ids}
    now = now or datetime.now(timezone.utc)
    local = now.astimezone(BRASILIA)
    with connect(database_path) as connection:
        settings = [
            dict(row)
            for row in connection.execute(
                "SELECT guild_id, game_time FROM championship_settings "
                "WHERE enabled = 1 AND paused_on IS NULL"
            )
        ]
    for setting in settings:
        hour, minute = (setting["game_time"] or DEFAULT_GAME_TIME).split(":")
        kickoff = local.replace(hour=int(hour), minute=int(minute), second=0, microsecond=0)
        if local < kickoff - LOCK_BEFORE_GAMES:
            continue
        edition = league.current_edition(setting["guild_id"], database_path)
        if edition is None or edition["status"] != "playing":
            continue
        pending = [
            game
            for game in league.due_fixtures(edition, now, database_path)
            if {game["home_user_id"], game["away_user_id"]} & users
        ]
        if pending:
            return (
                f"⏳ Transferências do grupo travadas: tem jogo do campeonato às "
                f"**{hour}:{minute}**. Elas voltam assim que os jogos do dia terminarem."
            )
    return None


# Elencos -----------------------------------------------------------------------------------


def _roster(connection: sqlite3.Connection, users: list[str]) -> list[dict[str, Any]]:
    marks = ", ".join("?" for _ in users)
    return [
        dict(row)
        for row in connection.execute(
            f"""
            SELECT p.id, COALESCE(NULLIF(p.display_name, ''), p.name) AS name, p.name AS full_name,
                   p.display_name, p.game_position, p.club_name,
                   {GAME_OVERALL_SQL} AS overall, {EFFECTIVE_OVERALL_SQL} AS rating,
                   o.discord_user_id AS owner_id, o.locked, o.pack_locked_until,
                   l.slot_order, l.position AS lineup_position
            FROM player_ownership AS o
            JOIN players AS p ON p.id = o.player_id
            LEFT JOIN player_balls AS b ON b.player_id = p.id
            LEFT JOIN team_lineups AS l
              ON l.player_id = p.id AND l.discord_user_id = o.discord_user_id
            WHERE o.discord_user_id IN ({marks}) AND p.game_position IS NOT NULL
            """,
            users,
        )
    ]


def _pack_locked(player: dict[str, Any], now: datetime) -> bool:
    until = player.get("pack_locked_until")
    return bool(until) and datetime.fromisoformat(until) > now


def _xi_rating(lineup: dict[int, tuple[int, str]], players: dict[int, dict], formation: str):
    """Média do quanto cada titular rende na vaga (vaga vazia conta 0)."""
    total = sum(
        slot_rating(int(players[pid]["rating"] or 0), players[pid]["game_position"], position)
        for pid, position in lineup.values()
    )
    return total / len(FORMATIONS[formation])


@dataclass
class Move:
    player: dict[str, Any]
    from_id: str
    to_id: str
    position: str


@dataclass
class AccountPlan:
    member: dict[str, Any]
    lineup: dict[int, tuple[int, str]]
    before: float
    after: float
    incoming: list[Move] = field(default_factory=list)
    outgoing: int = 0


@dataclass
class GroupPlan:
    group: dict[str, Any]
    accounts: list[AccountPlan]
    moves: list[Move]
    skipped: list[dict[str, Any]]  # contas sem time montado

    @property
    def moved_ids(self) -> set[int]:
        return {int(move.player["id"]) for move in self.moves}


def optimize_plan(
    group: dict[str, Any],
    database_path: str | Path = DATABASE_PATH,
    *,
    now: datetime | None = None,
) -> GroupPlan:
    """O melhor XI de cada conta na ordem do grupo (Principal primeiro), sem mexer em nada.

    Cada conta escolhe entre os jogadores que ainda não viraram titulares de uma conta
    acima dela. Desempate: fica quem já é da conta, e na mesma vaga."""
    now = now or datetime.now(timezone.utc)
    members = [m for m in group["members"] if m.get("formation")]
    skipped = [m for m in group["members"] if not m.get("formation")]
    users = [m["discord_user_id"] for m in group["members"]]
    with connect(database_path) as connection:
        roster = _roster(connection, users)
    players = {int(p["id"]): p for p in roster}
    taken: set[int] = set()
    accounts: list[AccountPlan] = []
    moves: list[Move] = []
    for member in members:
        user = member["discord_user_id"]
        mine = [p for p in roster if p["owner_id"] == user]
        current = {int(p["slot_order"]): int(p["id"]) for p in mine if p["slot_order"]}
        current_lineup = {
            int(p["slot_order"]): (int(p["id"]), p["lineup_position"])
            for p in mine
            if p["slot_order"]
        }
        available = [
            (int(p["id"]), p["game_position"], int(p["rating"] or 0))
            for p in roster
            if int(p["id"]) not in taken and (p["owner_id"] == user or not _pack_locked(p, now))
        ]
        lineup = best_lineup(available, member["formation"], current, {int(p["id"]) for p in mine})
        plan = AccountPlan(
            member=member,
            lineup=lineup,
            before=_xi_rating(current_lineup, players, member["formation"]),
            after=_xi_rating(lineup, players, member["formation"]),
        )
        for pid, position in lineup.values():
            taken.add(pid)
            player = players[pid]
            if player["owner_id"] != user:
                move = Move(player, player["owner_id"], user, position)
                plan.incoming.append(move)
                moves.append(move)
        accounts.append(plan)
    by_user = {plan.member["discord_user_id"]: plan for plan in accounts}
    for move in moves:
        if move.from_id in by_user:
            by_user[move.from_id].outgoing += 1
    for plan in accounts:
        plan.incoming.sort(key=lambda move: -int(move.player["rating"] or 0))
    return GroupPlan(group, accounts, moves, skipped)


def _transfer(connection: sqlite3.Connection, player_id: int, from_id: str, to_id: str) -> None:
    """Muda o dono (fica com 🔒 e bolas); erro se o jogador não está mais onde estava."""
    _leave_team(connection, from_id, player_id)
    moved = connection.execute(
        "UPDATE player_ownership SET discord_user_id = ?, acquired_at = CURRENT_TIMESTAMP "
        "WHERE player_id = ? AND discord_user_id = ?",
        (to_id, player_id, from_id),
    ).rowcount
    if not moved:
        raise GroupError("Os elencos mudaram desde o cálculo. Rode o comando de novo.")


def _check_manager(group: dict[str, Any], manager_id: str) -> None:
    if group["owner_user_id"] != manager_id:
        raise GroupError("Só o dono do grupo pode mexer nas outras contas.")


def apply_plan(
    manager_id: int | str,
    plan: GroupPlan,
    database_path: str | Path = DATABASE_PATH,
    *,
    now: datetime | None = None,
) -> int:
    """Faz as transferências do plano e escala cada conta. Devolve quantos mudaram de conta."""
    manager = str(manager_id)
    _check_manager(plan.group, manager)
    now = now or datetime.now(timezone.utc)
    users = [m["discord_user_id"] for m in plan.group["members"]]
    lock = transfer_lock(users, database_path, now=now) if plan.moves else None
    if lock:
        raise GroupError(lock)
    with connect(database_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        fresh = {int(p["id"]): p for p in _roster(connection, users)}
        for move in plan.moves:
            if _pack_locked(fresh.get(int(move.player["id"]), {}), now):
                raise GroupError(
                    f"📦 **{move.player['name']}** veio de pacote e ainda está travado."
                )
            _transfer(connection, int(move.player["id"]), move.from_id, move.to_id)
        for account in plan.accounts:
            user = account.member["discord_user_id"]
            starters = {pid for pid, _ in account.lineup.values()}
            owned = {
                int(row[0])
                for row in connection.execute(
                    "SELECT player_id FROM player_ownership WHERE discord_user_id = ?", (user,)
                )
            }
            if not starters <= owned:
                raise GroupError("Os elencos mudaram desde o cálculo. Rode o comando de novo.")
            connection.execute("DELETE FROM team_lineups WHERE discord_user_id = ?", (user,))
            connection.executemany(
                "INSERT INTO team_lineups (discord_user_id, slot_order, player_id, position) "
                "VALUES (?, ?, ?, ?)",
                [(user, slot, pid, position) for slot, (pid, position) in account.lineup.items()],
            )
            for column in ("captain_player_id", "free_kick_player_id", "penalty_player_id"):
                role = connection.execute(
                    f"SELECT {column} FROM team_tactics WHERE discord_user_id = ?", (user,)
                ).fetchone()
                if role is not None and role[0] is not None and int(role[0]) not in starters:
                    connection.execute(
                        f"UPDATE team_tactics SET {column} = NULL WHERE discord_user_id = ?",
                        (user,),
                    )
    return len(plan.moves)


def group_players(group: dict[str, Any], database_path: str | Path = DATABASE_PATH) -> list[dict]:
    """Todos os jogadores do grupo, com o dono (`owner_id`), do melhor para o pior."""
    users = [m["discord_user_id"] for m in group["members"]]
    with connect(database_path) as connection:
        roster = _roster(connection, users)
    return sorted(roster, key=lambda p: (-int(p["rating"] or 0), p["name"]))


def move_player(
    manager_id: int | str,
    group: dict[str, Any],
    player_id: int,
    to_id: int | str,
    database_path: str | Path = DATABASE_PATH,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Passa um jogador de uma conta do grupo para outra. Devolve o jogador (com `owner_id`
    antigo)."""
    manager, target = str(manager_id), str(to_id)
    _check_manager(group, manager)
    now = now or datetime.now(timezone.utc)
    users = [m["discord_user_id"] for m in group["members"]]
    if target not in users:
        raise GroupError("Essa conta não está no seu grupo.")
    lock = transfer_lock(users, database_path, now=now)
    if lock:
        raise GroupError(lock)
    with connect(database_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        player = next((p for p in _roster(connection, users) if int(p["id"]) == player_id), None)
        if player is None:
            raise GroupError("Esse jogador não está em nenhuma conta do grupo.")
        if player["owner_id"] == target:
            raise GroupError(f"**{player['name']}** já é dessa conta.")
        if _pack_locked(player, now):
            raise GroupError(f"📦 **{player['name']}** veio de pacote e ainda está travado.")
        _transfer(connection, player_id, player["owner_id"], target)
    return player


# Venda em massa ----------------------------------------------------------------------------


def group_sale_ids(
    group: dict[str, Any],
    accounts: Iterable[str],
    database_path: str | Path = DATABASE_PATH,
    *,
    keep: set[int] | frozenset[int] = frozenset(),
    **filters: Any,
) -> dict[str, list[int]]:
    """Reservas sem 🔒 de cada conta que batem com o filtro (como a venda rápida), menos os
    de `keep` (quem seria titular em outra conta). Conta -> IDs, só as que têm algum."""
    found = {}
    for user in accounts:
        ids = [pid for pid in quick_sale_ids(user, database_path, **filters) if pid not in keep]
        if ids:
            found[user] = ids
    return found


# Avisos ------------------------------------------------------------------------------------


def promotion_hint(
    user_id: int | str,
    player_id: int,
    equipped_here: bool,
    database_path: str | Path = DATABASE_PATH,
) -> dict[str, Any] | None:
    """Recém-contratado que seria titular em outra conta do grupo: a conta e a vaga.

    Olha as contas acima de quem contratou e, se ele não entrou no time de quem contratou,
    também as de baixo. None se não serve em nenhuma (ou se não há grupo)."""
    group = group_of(user_id, database_path)
    if group is None or len(group["members"]) < 2:
        return None
    with connect(database_path) as connection:
        row = connection.execute(
            f"""
            SELECT p.game_position, {EFFECTIVE_OVERALL_SQL} AS rating
            FROM players AS p LEFT JOIN player_balls AS b ON b.player_id = p.id
            WHERE p.id = ?
            """,
            (player_id,),
        ).fetchone()
    if row is None:
        return None
    mine = next(m for m in group["members"] if m["discord_user_id"] == str(user_id))
    for member in group["members"]:
        if member["discord_user_id"] == str(user_id):
            continue
        if member["rank"] > mine["rank"] and equipped_here:
            continue
        upgrade = upgrade_slot(
            member["discord_user_id"], row["game_position"], int(row["rating"] or 0), database_path
        )
        if upgrade is not None:
            return {"member": member, **upgrade}
    return None
