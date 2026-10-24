"""Gerenciamento de elencos, formações e imagem local do time."""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

from src.db import DATABASE_PATH, connect
from src.game import register_user
from src.positions import fit, slot_rating
from src.ratings import EFFECTIVE_OVERALL_SQL, GAME_OVERALL_SQL

FORMATIONS: dict[str, tuple[str, ...]] = {
    "4-4-2": ("GOL", "ZAG", "ZAG", "LD", "LE", "MC", "MC", "MEI", "MEI", "CA", "CA"),
    "4-3-3": ("GOL", "ZAG", "ZAG", "LD", "LE", "MC", "MC", "MEI", "PE", "PD", "CA"),
    "3-5-2": ("GOL", "ZAG", "ZAG", "ZAG", "LD", "LE", "MC", "MC", "MEI", "CA", "CA"),
    "4-2-3-1": ("GOL", "ZAG", "ZAG", "LD", "LE", "MC", "MC", "MEI", "PE", "PD", "CA"),
    "5-3-2": ("GOL", "ZAG", "ZAG", "ZAG", "LD", "LE", "MC", "MC", "MEI", "CA", "CA"),
    "4-1-4-1": ("GOL", "ZAG", "ZAG", "LD", "LE", "MC", "MC", "MEI", "MEI", "PE", "CA"),
    "3-4-3": ("GOL", "ZAG", "ZAG", "ZAG", "LD", "LE", "MC", "MEI", "PE", "PD", "CA"),
    "4-5-1": ("GOL", "ZAG", "ZAG", "LD", "LE", "MC", "MC", "MEI", "MEI", "PE", "CA"),
    "5-4-1": ("GOL", "ZAG", "ZAG", "ZAG", "LD", "LE", "MC", "MC", "MEI", "PE", "CA"),
    "4-3-2-1": ("GOL", "ZAG", "ZAG", "LD", "LE", "MC", "MC", "MEI", "PE", "PD", "CA"),
}

STYLE_LABELS = {
    "balanced": "equilibrado",
    "attack": "ataque total",
    "counter": "contra-ataque",
    "press": "pressão alta",
    "park": "retranca",
}

POSITION_GROUPS = {
    "GOL": "G",
    "ZAG": "D",
    "LD": "D",
    "LE": "D",
    "MC": "M",
    "MEI": "M",
    "PE": "A",
    "PD": "A",
    "CA": "A",
}


class TeamError(Exception):
    """Erro de validação da escalação ou perfil do time."""


def ensure_team(
    discord_user_id: int | str,
    display_name: str,
    database_path: str | Path = DATABASE_PATH,
) -> None:
    user_id = str(discord_user_id)
    register_user(user_id, display_name, database_path)
    with connect(database_path) as connection:
        connection.execute(
            """
            INSERT INTO game_teams (discord_user_id, team_name)
            VALUES (?, ?)
            ON CONFLICT(discord_user_id) DO NOTHING
            """,
            (user_id, f"{display_name} FC"[:50]),
        )
        connection.execute(
            """
            INSERT INTO team_tactics (discord_user_id)
            VALUES (?)
            ON CONFLICT(discord_user_id) DO NOTHING
            """,
            (user_id,),
        )


def get_team(
    discord_user_id: int | str,
    database_path: str | Path = DATABASE_PATH,
) -> dict[str, Any] | None:
    with connect(database_path) as connection:
        row = connection.execute(
            """
            SELECT t.*, x.formation, x.style, x.captain_player_id,
                   x.free_kick_player_id, x.penalty_player_id,
                   (SELECT c.photo_path FROM coaches AS c
                    WHERE c.name = t.coach_name ORDER BY c.id LIMIT 1) AS coach_photo_path
            FROM game_teams AS t
            LEFT JOIN team_tactics AS x
              ON x.discord_user_id = t.discord_user_id
            WHERE t.discord_user_id = ?
            """,
            (str(discord_user_id),),
        ).fetchone()
    return dict(row) if row else None


def set_team_field(
    discord_user_id: int | str,
    field: str,
    value: str,
    database_path: str | Path = DATABASE_PATH,
) -> None:
    """Nome e cor são livres; cosméticos (técnico, liga, estádio…) só se forem da pessoa."""
    from src.cosmetics import COSMETIC_KINDS, CosmeticError, equip

    cosmetic_kind = next(
        (kind for kind, (_, _, column, _) in COSMETIC_KINDS.items() if column == field), None
    )
    if cosmetic_kind is not None:
        try:
            equip(discord_user_id, cosmetic_kind, value, database_path)
        except CosmeticError as error:
            raise TeamError(str(error)) from error
        return
    if field not in {"team_name", "color"}:
        raise ValueError("Campo de time inválido.")
    if field == "team_name" and not 1 <= len(value.strip()) <= 50:
        raise TeamError("O nome do time deve ter entre 1 e 50 caracteres.")
    if field == "color":
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", value):
            raise TeamError("Use uma cor hexadecimal, por exemplo `#2E8B57`.")
        value = value.upper()
    with connect(database_path) as connection:
        cursor = connection.execute(
            f"""
            UPDATE game_teams
            SET {field} = ?, updated_at = CURRENT_TIMESTAMP
            WHERE discord_user_id = ?
            """,
            (value.strip(), str(discord_user_id)),
        )
        if cursor.rowcount == 0:
            raise TeamError("Seu time ainda não foi criado.")


def set_formation(
    discord_user_id: int | str,
    formation: str,
    database_path: str | Path = DATABASE_PATH,
    *,
    drop_incompatible: bool = False,
) -> int:
    """Troca a formação; devolve quantos titulares saíram por falta de vaga."""
    normalized = formation.strip()
    if normalized not in FORMATIONS:
        raise TeamError("Formação inválida. Escolha uma: " + ", ".join(FORMATIONS) + ".")
    user_id = str(discord_user_id)
    with connect(database_path) as connection:
        team = connection.execute(
            "SELECT 1 FROM game_teams WHERE discord_user_id = ?",
            (user_id,),
        ).fetchone()
        if team is None:
            raise TeamError("Configure seu time primeiro com `mm!time`.")
        lineup = connection.execute(
            """
            SELECT position, COUNT(*) AS amount FROM team_lineups
            WHERE discord_user_id = ? GROUP BY position
            """,
            (user_id,),
        ).fetchall()
        current_counts = Counter({row["position"]: int(row["amount"]) for row in lineup})
        allowed_counts = Counter(FORMATIONS[normalized])
        if not drop_incompatible and any(
            current_counts[position] > allowed_counts[position] for position in current_counts
        ):
            raise TeamError(
                "A formação nova não comporta sua escalação atual. "
                "Remova os jogadores incompatíveis e tente novamente."
            )
        connection.execute(
            "UPDATE team_tactics SET formation = ? WHERE discord_user_id = ?",
            (normalized, user_id),
        )
        available_slots: dict[str, list[int]] = {}
        for slot, position in enumerate(FORMATIONS[normalized], start=1):
            available_slots.setdefault(position, []).append(slot)
        players = connection.execute(
            """
            SELECT slot_order, player_id, position FROM team_lineups
            WHERE discord_user_id = ? ORDER BY slot_order
            """,
            (user_id,),
        ).fetchall()
        connection.execute(
            "DELETE FROM team_lineups WHERE discord_user_id = ?",
            (user_id,),
        )
        dropped = 0
        for player in players:
            if not available_slots.get(player["position"]):
                dropped += 1
                continue
            slot = available_slots[player["position"]].pop(0)
            connection.execute(
                """
                INSERT INTO team_lineups
                    (discord_user_id, slot_order, player_id, position)
                VALUES (?, ?, ?, ?)
                """,
                (user_id, slot, player["player_id"], player["position"]),
            )
        return dropped


def set_team_style(
    discord_user_id: int | str,
    style: str,
    database_path: str | Path = DATABASE_PATH,
) -> None:
    aliases = {
        "equilibrado": "balanced",
        "balanced": "balanced",
        "ataque": "attack",
        "ataque total": "attack",
        "attack": "attack",
        "contra": "counter",
        "contra-ataque": "counter",
        "counter": "counter",
        "pressao": "press",
        "pressão": "press",
        "pressao alta": "press",
        "pressão alta": "press",
        "press": "press",
        "retranca": "park",
        "park": "park",
    }
    normalized = aliases.get(style.casefold().strip())
    if normalized is None:
        raise TeamError(
            "Estilo inválido: equilibrado, ataque, contra-ataque, pressão alta ou retranca."
        )
    with connect(database_path) as connection:
        cursor = connection.execute(
            "UPDATE team_tactics SET style = ? WHERE discord_user_id = ?",
            (normalized, str(discord_user_id)),
        )
        if cursor.rowcount == 0:
            raise TeamError("Configure seu time primeiro com `mm!time`.")


def scale_player(
    discord_user_id: int | str,
    player_id: int,
    database_path: str | Path = DATABASE_PATH,
) -> int:
    user_id = str(discord_user_id)
    with connect(database_path) as connection:
        ownership = connection.execute(
            """
            SELECT p.game_position
            FROM player_ownership AS o
            JOIN players AS p ON p.id = o.player_id
            WHERE o.discord_user_id = ? AND p.id = ?
            """,
            (user_id, player_id),
        ).fetchone()
        if ownership is None:
            raise TeamError("Esse jogador não pertence ao seu elenco.")
        position = ownership["game_position"]
        if position not in POSITION_GROUPS:
            raise TeamError("Esse jogador não tem uma posição válida para escalar.")
        team = connection.execute(
            """
            SELECT t.discord_user_id, x.formation
            FROM game_teams AS t
            JOIN team_tactics AS x ON x.discord_user_id = t.discord_user_id
            WHERE t.discord_user_id = ?
            """,
            (user_id,),
        ).fetchone()
        if team is None:
            raise TeamError("Configure seu time primeiro com `mm!time`.")
        if connection.execute(
            """
            SELECT 1 FROM team_lineups
            WHERE discord_user_id = ? AND player_id = ?
            """,
            (user_id, player_id),
        ).fetchone():
            raise TeamError("Esse jogador já está escalado.")
        positions = FORMATIONS[team["formation"]]
        occupied = {
            int(row["slot_order"])
            for row in connection.execute(
                "SELECT slot_order FROM team_lineups WHERE discord_user_id = ?",
                (user_id,),
            )
        }
        # Vaga livre da própria posição; senão, a vaga livre onde ele rende mais.
        free = [
            (fit(position, required), -index, index, required)
            for index, required in enumerate(positions, start=1)
            if index not in occupied and fit(position, required)
        ]
        if not free:
            raise TeamError(f"A formação {team['formation']} não tem vaga para mais um {position}.")
        _, _, slot_order, required = max(free)
        connection.execute(
            """
            INSERT INTO team_lineups (discord_user_id, slot_order, player_id, position)
            VALUES (?, ?, ?, ?)
            """,
            (user_id, slot_order, player_id, required),
        )
        return slot_order


def remove_player_from_lineup(
    discord_user_id: int | str,
    player_id: int,
    database_path: str | Path = DATABASE_PATH,
) -> bool:
    with connect(database_path) as connection:
        cursor = connection.execute(
            """
            DELETE FROM team_lineups
            WHERE discord_user_id = ? AND player_id = ?
            """,
            (str(discord_user_id), player_id),
        )
    return cursor.rowcount > 0


def set_team_role(
    discord_user_id: int | str,
    role: str,
    player_id: int,
    database_path: str | Path = DATABASE_PATH,
) -> None:
    columns = {
        "capitao": "captain_player_id",
        "falta": "free_kick_player_id",
        "penalti": "penalty_player_id",
    }
    column = columns.get(role)
    if column is None:
        raise ValueError("Função tática inválida.")
    with connect(database_path) as connection:
        row = connection.execute(
            """
            SELECT 1 FROM team_lineups
            WHERE discord_user_id = ? AND player_id = ?
            """,
            (str(discord_user_id), player_id),
        ).fetchone()
        if row is None:
            raise TeamError("O jogador precisa estar na escalação para essa função.")
        connection.execute(
            f"UPDATE team_tactics SET {column} = ? WHERE discord_user_id = ?",
            (player_id, str(discord_user_id)),
        )


def get_lineup(
    discord_user_id: int | str,
    database_path: str | Path = DATABASE_PATH,
) -> list[dict[str, Any]]:
    with connect(database_path) as connection:
        rows = connection.execute(
            f"""
            SELECT l.slot_order, l.position, p.id, p.name, p.display_name,
                   {GAME_OVERALL_SQL} AS overall, p.club_name, p.photo_path,
                   p.game_position AS natural_position,
                   {EFFECTIVE_OVERALL_SQL} AS effective_overall
            FROM team_lineups AS l
            JOIN players AS p ON p.id = l.player_id
            LEFT JOIN player_balls AS b ON b.player_id = p.id
            WHERE l.discord_user_id = ?
            ORDER BY l.slot_order
            """,
            (str(discord_user_id),),
        ).fetchall()
    lineup = []
    for row in rows:
        player = dict(row)
        # Quanto ele rende nessa vaga (igual ao overall quando joga na própria posição).
        player["slot_overall"] = (
            slot_rating(player["effective_overall"], player["natural_position"], player["position"])
            if player["effective_overall"] is not None
            else None
        )
        lineup.append(player)
    return lineup


def get_team_statistics(
    discord_user_id: int | str,
    database_path: str | Path = DATABASE_PATH,
) -> dict[str, Any]:
    """Forma (últimos 5), títulos, jogos e artilheiro para o cartão do clube.

    Usa a mesma conta do `mm!stats` (`src.team_stats`): amistosos, o antigo campeonato
    diário, Pintos Corridos e Copa IOS. Antes só o campeonato diário contava, e a forma
    ignorava os jogos novos."""
    from src.team_stats import compute_stats, match_records

    stats = compute_stats(match_records(discord_user_id, database_path))
    return {
        "matches": stats.matches,
        "form": stats.form,
        "trophies": stats.titles,
        "top_scorer": stats.top_scorers[0] if stats.top_scorers else None,
    }


def get_valid_lineup(
    discord_user_id: int | str,
    database_path: str | Path = DATABASE_PATH,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    team = get_team(discord_user_id, database_path)
    if team is None:
        raise TeamError("Esse time ainda não foi criado.")
    players = get_lineup(discord_user_id, database_path)
    expected = Counter(FORMATIONS[team["formation"]])
    actual = Counter(player["position"] for player in players)
    if len(players) != 11 or actual != expected:
        raise TeamError(
            f"A escalação de {team['team_name']} está incompleta para "
            f"{team['formation']}: faltam titulares ou posições corretas."
        )
    if any(player["overall"] is None for player in players):
        raise TeamError("Todos os titulares precisam ter overall definido.")
    return team, players


def formation_slots(formation: str) -> tuple[str, ...]:
    try:
        return FORMATIONS[formation]
    except KeyError as error:
        raise TeamError(f"Formação desconhecida: {formation}") from error


def set_lineup_slot(
    discord_user_id: int | str,
    slot_order: int,
    player_id: int,
    database_path: str | Path = DATABASE_PATH,
) -> dict[str, Any]:
    """Coloca o jogador numa vaga; quem estava nela sai e ele deixa a vaga antiga."""
    user_id = str(discord_user_id)
    with connect(database_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        team = connection.execute(
            "SELECT formation FROM team_tactics WHERE discord_user_id = ?",
            (user_id,),
        ).fetchone()
        if team is None:
            raise TeamError("Configure seu time primeiro com `mm!time`.")
        positions = FORMATIONS[team["formation"]]
        if not 1 <= slot_order <= len(positions):
            raise TeamError("Vaga inválida para essa formação.")
        required = positions[slot_order - 1]
        player = connection.execute(
            """
            SELECT p.id, COALESCE(NULLIF(p.display_name, ''), p.name) AS name,
                   p.game_position
            FROM player_ownership AS o
            JOIN players AS p ON p.id = o.player_id
            WHERE o.discord_user_id = ? AND p.id = ?
            """,
            (user_id, player_id),
        ).fetchone()
        if player is None:
            raise TeamError("Esse jogador não pertence ao seu elenco.")
        if not fit(player["game_position"], required):
            raise TeamError(
                f"{player['name']} joga de {player['game_position']} e não pode atuar de "
                f"{required}."
            )
        connection.execute(
            "DELETE FROM team_lineups WHERE discord_user_id = ? AND (slot_order = ? OR player_id = ?)",
            (user_id, slot_order, player_id),
        )
        connection.execute(
            """
            INSERT INTO team_lineups (discord_user_id, slot_order, player_id, position)
            VALUES (?, ?, ?, ?)
            """,
            (user_id, slot_order, player_id, required),
        )
        return dict(player)


def clear_lineup_slot(
    discord_user_id: int | str,
    slot_order: int,
    database_path: str | Path = DATABASE_PATH,
) -> bool:
    with connect(database_path) as connection:
        cursor = connection.execute(
            "DELETE FROM team_lineups WHERE discord_user_id = ? AND slot_order = ?",
            (str(discord_user_id), slot_order),
        )
    return cursor.rowcount > 0


def upgrade_slot(
    discord_user_id: int | str,
    natural: str | None,
    rating: int,
    database_path: str | Path = DATABASE_PATH,
) -> dict[str, Any] | None:
    """A vaga da escalação que um jogador (`natural`, `rating` com bolas) mais melhora.

    Compara o quanto ele renderia em cada vaga com o titular de lá (vaga vazia conta 0).
    Devolve slot, posição, quem sai (ou None) e os dois valores; None se não melhora nada
    ou se o time ainda não foi montado."""
    with connect(database_path) as connection:
        team = connection.execute(
            "SELECT formation FROM team_tactics WHERE discord_user_id = ?",
            (str(discord_user_id),),
        ).fetchone()
    if team is None or not natural or not rating:
        return None
    starters = {
        int(player["slot_order"]): player for player in get_lineup(discord_user_id, database_path)
    }
    best = None
    for slot, position in enumerate(FORMATIONS[team["formation"]], start=1):
        if not fit(natural, position):
            continue
        new = slot_rating(rating, natural, position)
        current = starters.get(slot)
        old = int(current["slot_overall"] or 0) if current else 0
        if new > old and (best is None or new - old > best["new"] - best["old"]):
            best = {
                "slot": slot,
                "position": position,
                "replaces": current,
                "old": old,
                "new": new,
            }
    return best


def equip_if_better(
    discord_user_id: int | str, player_id: int, database_path: str | Path = DATABASE_PATH
) -> dict[str, Any] | None:
    """Recém-contratado melhor que um titular entra no lugar dele (quem sai perde capitão,
    falta e pênalti). Devolve a troca feita (como `upgrade_slot`) ou None."""
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
    upgrade = upgrade_slot(
        discord_user_id, row["game_position"], int(row["rating"] or 0), database_path
    )
    if upgrade is None:
        return None
    set_lineup_slot(discord_user_id, upgrade["slot"], player_id, database_path)
    if upgrade["replaces"]:
        with connect(database_path) as connection:
            for column in ("captain_player_id", "free_kick_player_id", "penalty_player_id"):
                connection.execute(
                    f"UPDATE team_tactics SET {column} = NULL "
                    f"WHERE discord_user_id = ? AND {column} = ?",
                    (str(discord_user_id), upgrade["replaces"]["id"]),
                )
    return upgrade


def best_assignment(scores: list[list[float | None]]) -> list[tuple[int, int]]:
    """Linha -> coluna com a maior soma de pontos, cada coluna usada uma vez (húngaro).

    `None` = combinação proibida. Linhas sem nenhuma coluna possível ficam de fora.
    Devolve pares `(linha, coluna)`.
    """
    rows = len(scores)
    columns = len(scores[0]) if scores else 0
    if not rows or not columns:
        return []
    forbidden = 10**6
    # Colunas fictícias garantem colunas >= linhas (vaga que fica vazia).
    width = max(columns, rows) + rows
    cost = [
        [
            (-value if value is not None else forbidden) if column < columns else forbidden / 2
            for column, value in enumerate(row + [None] * (width - columns))
        ]
        for row in scores
    ]
    # Implementação clássica O(n²·m), índices a partir de 1.
    u = [0.0] * (rows + 1)
    v = [0.0] * (width + 1)
    match = [0] * (width + 1)
    way = [0] * (width + 1)
    for row in range(1, rows + 1):
        match[0] = row
        column0 = 0
        minimum = [float("inf")] * (width + 1)
        used = [False] * (width + 1)
        while True:
            used[column0] = True
            row0 = match[column0]
            delta = float("inf")
            column1 = 0
            for column in range(1, width + 1):
                if used[column]:
                    continue
                current = cost[row0 - 1][column - 1] - u[row0] - v[column]
                if current < minimum[column]:
                    minimum[column] = current
                    way[column] = column0
                if minimum[column] < delta:
                    delta = minimum[column]
                    column1 = column
            for column in range(width + 1):
                if used[column]:
                    u[match[column]] += delta
                    v[column] -= delta
                else:
                    minimum[column] -= delta
            column0 = column1
            if match[column0] == 0:
                break
        while column0:
            column1 = way[column0]
            match[column0] = match[column1]
            column0 = column1
    pairs = []
    for column in range(1, width + 1):
        row = match[column]
        if row and column <= columns and scores[row - 1][column - 1] is not None:
            pairs.append((row - 1, column - 1))
    return pairs


def best_lineup(
    roster: Iterable[tuple[int, str | None, int]],
    formation: str,
    current: dict[int, int] | None = None,
    preferred: set[int] | frozenset[int] = frozenset(),
) -> dict[int, tuple[int, str]]:
    """A escalação de maior soma para `formation` com o elenco `roster` (id, posição, overall).

    Desempates (nunca mudam a soma): quem já está na vaga (`current`, vaga -> jogador) e quem
    está em `preferred`. Devolve vaga -> (jogador, posição)."""
    current = current or {}
    slots = list(enumerate(FORMATIONS[formation], start=1))
    # Só os 8 melhores de cada vaga entram na conta: o resultado é o mesmo e fica rápido.
    pool: dict[int, tuple[str, int]] = {}
    for _, position in slots:
        ranked = sorted(
            (
                (slot_rating(rating, natural, position), player_id, natural, rating)
                for player_id, natural, rating in roster
                if fit(natural, position)
            ),
            reverse=True,
        )
        for _, player_id, natural, rating in ranked[:8]:
            pool[player_id] = (natural, rating)
    players = sorted(pool)
    scores = [
        [
            (
                slot_rating(pool[player_id][1], pool[player_id][0], position)
                + (0.01 if current.get(slot) == player_id else 0.0)
                + (0.02 if player_id in preferred else 0.0)
                if fit(pool[player_id][0], position)
                else None
            )
            for player_id in players
        ]
        for slot, position in slots
    ]
    best: dict[int, tuple[int, str]] = {}
    for row, column in best_assignment(scores):
        slot, position = slots[row]
        best[slot] = (players[column], position)
    return best


def auto_fill_lineup(
    discord_user_id: int | str,
    database_path: str | Path = DATABASE_PATH,
) -> int:
    """Monta a escalação que rende mais, aceitando jogadores fora de posição.

    Cada jogador vale `overall com bolas × rendimento na vaga` (`src.positions`): um
    ponta-esquerda bem melhor pode ocupar a vaga do ponta-direita fraco. A distribuição
    é a de maior soma (algoritmo húngaro), sem repetir jogador. Quem continua no time
    fica na mesma vaga; quem sai perde capitão/falta/pênalti. Devolve quantos
    jogadores entraram no time.
    """
    user_id = str(discord_user_id)
    with connect(database_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        team = connection.execute(
            "SELECT formation FROM team_tactics WHERE discord_user_id = ?",
            (user_id,),
        ).fetchone()
        if team is None:
            raise TeamError("Configure seu time primeiro com `mm!time`.")
        current = {
            int(row["slot_order"]): int(row["player_id"])
            for row in connection.execute(
                "SELECT slot_order, player_id FROM team_lineups WHERE discord_user_id = ?",
                (user_id,),
            )
        }
        roster = [
            (int(row["id"]), row["game_position"], int(row["rating"] or 0))
            for row in connection.execute(
                f"""
                SELECT p.id, p.game_position, {EFFECTIVE_OVERALL_SQL} AS rating
                FROM player_ownership AS o
                JOIN players AS p ON p.id = o.player_id
                LEFT JOIN player_balls AS b ON b.player_id = p.id
                WHERE o.discord_user_id = ? AND p.game_position IS NOT NULL
                """,
                (user_id,),
            )
        ]
        best = best_lineup(roster, team["formation"], current)
        changed = len({player_id for player_id, _ in best.values()} - set(current.values()))
        connection.execute("DELETE FROM team_lineups WHERE discord_user_id = ?", (user_id,))
        connection.executemany(
            """
            INSERT INTO team_lineups (discord_user_id, slot_order, player_id, position)
            VALUES (?, ?, ?, ?)
            """,
            [(user_id, slot, player_id, position) for slot, (player_id, position) in best.items()],
        )
        starters = {player_id for player_id, _ in best.values()}
        for column in ("captain_player_id", "free_kick_player_id", "penalty_player_id"):
            role = connection.execute(
                f"SELECT {column} FROM team_tactics WHERE discord_user_id = ?", (user_id,)
            ).fetchone()[0]
            if role is not None and int(role) not in starters:
                connection.execute(
                    f"UPDATE team_tactics SET {column} = NULL WHERE discord_user_id = ?",
                    (user_id,),
                )
        return changed
