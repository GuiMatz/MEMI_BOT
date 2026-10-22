"""Gerenciamento de elencos, formações e imagem local do time."""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from src.db import DATABASE_PATH, connect
from src.game import register_user
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
    }
    normalized = aliases.get(style.casefold().strip())
    if normalized is None:
        raise TeamError("Estilo inválido: equilibrado, ataque ou contra-ataque.")
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
        slot_order = next(
            (
                index
                for index, required in enumerate(positions, start=1)
                if required == position and index not in occupied
            ),
            None,
        )
        if slot_order is None:
            raise TeamError(f"A formação {team['formation']} não tem vaga para mais um {position}.")
        connection.execute(
            """
            INSERT INTO team_lineups (discord_user_id, slot_order, player_id, position)
            VALUES (?, ?, ?, ?)
            """,
            (user_id, slot_order, player_id, position),
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
                   {EFFECTIVE_OVERALL_SQL} AS effective_overall
            FROM team_lineups AS l
            JOIN players AS p ON p.id = l.player_id
            LEFT JOIN player_balls AS b ON b.player_id = p.id
            WHERE l.discord_user_id = ?
            ORDER BY l.slot_order
            """,
            (str(discord_user_id),),
        ).fetchall()
    return [dict(row) for row in rows]


def get_team_statistics(
    discord_user_id: int | str,
    database_path: str | Path = DATABASE_PATH,
) -> dict[str, Any]:
    user_id = str(discord_user_id)
    with connect(database_path) as connection:
        fixtures = [
            dict(row)
            for row in connection.execute(
                """
                SELECT f.*, c.status AS championship_status
                FROM championship_fixtures AS f
                JOIN championships AS c ON c.id = f.championship_id
                WHERE c.status = 'completed'
                  AND (f.home_user_id = ? OR f.away_user_id = ?)
                  AND f.home_goals IS NOT NULL
                ORDER BY f.played_at DESC
                """,
                (user_id, user_id),
            )
        ]
        trophies = int(
            connection.execute(
                """
                SELECT COUNT(*) FROM championship_fixtures AS f
                JOIN championships AS c ON c.id = f.championship_id
                WHERE c.status = 'completed' AND f.is_final = 1
                  AND f.winner_user_id = ?
                """,
                (user_id,),
            ).fetchone()[0]
        )
    player_goals: Counter[int] = Counter()
    player_appearances: Counter[int] = Counter()
    player_names: dict[int, str] = {}
    form: list[str] = []
    for fixture in fixtures:
        is_home = fixture["home_user_id"] == user_id
        own_goals = int(fixture["home_goals"] if is_home else fixture["away_goals"])
        opponent_goals = int(fixture["away_goals"] if is_home else fixture["home_goals"])
        if fixture["is_final"] and fixture["winner_user_id"]:
            outcome = "V" if fixture["winner_user_id"] == user_id else "D"
        else:
            outcome = (
                "V" if own_goals > opponent_goals else ("D" if own_goals < opponent_goals else "E")
            )
        form.append(outcome)
        events = json.loads(fixture["events_json"] or "{}")
        player_names.update(
            {int(player_id): name for player_id, name in events.get("player_names", {}).items()}
        )
        lineup_key = "home_lineup" if is_home else "away_lineup"
        player_appearances.update(int(player_id) for player_id in events.get(lineup_key, []))
        for goal in events.get("goals", []):
            if goal["side"] == ("home" if is_home else "away"):
                player_id = int(goal["scorer_id"])
                player_goals[player_id] += 1
                player_names[player_id] = goal["scorer_name"]
    top_scorer_id = min(
        player_goals,
        key=lambda player_id: (-player_goals[player_id], player_names[player_id].casefold()),
        default=None,
    )
    most_used_id = min(
        player_appearances,
        key=lambda player_id: (
            -player_appearances[player_id],
            player_names.get(player_id, "").casefold(),
        ),
        default=None,
    )
    return {
        "matches": len(fixtures),
        "form": form[:5],
        "trophies": trophies,
        "top_scorer": (
            (player_names[top_scorer_id], player_goals[top_scorer_id])
            if top_scorer_id is not None
            else None
        ),
        "most_appearances": (
            (
                player_names.get(most_used_id, f"Jogador {most_used_id}"),
                player_appearances[most_used_id],
            )
            if most_used_id is not None
            else None
        ),
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
        if player["game_position"] != required:
            raise TeamError(
                f"{player['name']} joga de {player['game_position']}; essa vaga pede {required}."
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


def auto_fill_lineup(
    discord_user_id: int | str,
    database_path: str | Path = DATABASE_PATH,
) -> int:
    """Remonta a escalação com os maiores overalls (com bolas) de cada posição.

    Troca também titulares mais fracos, não só vagas vazias. Quem sai do time perde a
    função de capitão/falta/pênalti; quem continua fica na mesma vaga. Devolve quantos
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
        candidates: dict[str, list[int]] = {}
        for row in connection.execute(
            f"""
            SELECT p.id, p.game_position
            FROM player_ownership AS o
            JOIN players AS p ON p.id = o.player_id
            LEFT JOIN player_balls AS b ON b.player_id = p.id
            WHERE o.discord_user_id = ? AND p.game_position IS NOT NULL
            ORDER BY {EFFECTIVE_OVERALL_SQL} DESC, p.id
            """,
            (user_id,),
        ):
            candidates.setdefault(row["game_position"], []).append(int(row["id"]))
        slots_by_position: dict[str, list[int]] = {}
        for slot, position in enumerate(FORMATIONS[team["formation"]], start=1):
            slots_by_position.setdefault(position, []).append(slot)
        best: dict[int, tuple[int, str]] = {}
        for position, slots in slots_by_position.items():
            chosen = candidates.get(position, [])[: len(slots)]
            # Quem continua no time fica na mesma vaga; os reforços ocupam as que sobram.
            free = []
            for slot in slots:
                if current.get(slot) in chosen:
                    best[slot] = (current[slot], position)
                else:
                    free.append(slot)
            newcomers = [
                player_id
                for player_id in chosen
                if player_id not in {kept for kept, _ in best.values()}
            ]
            for slot, player_id in zip(free, newcomers):
                best[slot] = (player_id, position)
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
