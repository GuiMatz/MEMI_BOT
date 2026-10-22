"""Campeonato diário serial com fase todos-contra-todos e final."""

from __future__ import annotations

import json
import random
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from src.clock import BRASILIA
from src.db import DATABASE_PATH, connect
from src.income import add_box_office
from src.simulation import penalty_shootout_kicks, simulate_match
from src.teams import TeamError, get_valid_lineup

CHAMPIONSHIP_TIMEZONE = BRASILIA
CHAMPION_PRIZE = 500


class ChampionshipError(Exception):
    """Erro de estado ou elegibilidade do campeonato diário."""


def competition_date(now: datetime | None = None) -> str:
    value = now or datetime.now(timezone.utc)
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("now deve incluir um fuso horário.")
    return value.astimezone(CHAMPIONSHIP_TIMEZONE).date().isoformat()


def register_team(
    guild_id: int | str,
    discord_user_id: int | str,
    *,
    now: datetime | None = None,
    database_path: str | Path = DATABASE_PATH,
) -> int:
    team, _ = get_valid_lineup(discord_user_id, database_path)
    date = competition_date(now)
    with connect(database_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            INSERT INTO championships (guild_id, competition_date, timezone)
            VALUES (?, ?, ?)
            ON CONFLICT(guild_id, competition_date) DO NOTHING
            """,
            (str(guild_id), date, "America/Sao_Paulo"),
        )
        championship = connection.execute(
            """
            SELECT id, status FROM championships
            WHERE guild_id = ? AND competition_date = ?
            """,
            (str(guild_id), date),
        ).fetchone()
        if championship["status"] != "registration":
            raise ChampionshipError("As inscrições desse campeonato já foram encerradas.")
        connection.execute(
            """
            INSERT INTO championship_entries
                (championship_id, discord_user_id, team_name)
            VALUES (?, ?, ?)
            ON CONFLICT(championship_id, discord_user_id)
            DO UPDATE SET team_name = excluded.team_name
            """,
            (championship["id"], str(discord_user_id), team["team_name"]),
        )
        return int(championship["id"])


def _standings(
    entries: list[dict[str, str]],
    fixtures: list[dict],
) -> list[dict]:
    table = {
        entry["discord_user_id"]: {
            **entry,
            "played": 0,
            "wins": 0,
            "draws": 0,
            "losses": 0,
            "gf": 0,
            "ga": 0,
            "points": 0,
            "h2h_points": 0,
            "h2h_gd": 0,
        }
        for entry in entries
    }
    for fixture in fixtures:
        home = table[fixture["home_user_id"]]
        away = table[fixture["away_user_id"]]
        home_goals = int(fixture["home_goals"])
        away_goals = int(fixture["away_goals"])
        home["played"] += 1
        away["played"] += 1
        home["gf"] += home_goals
        home["ga"] += away_goals
        away["gf"] += away_goals
        away["ga"] += home_goals
        if home_goals > away_goals:
            home["wins"] += 1
            home["points"] += 3
            home["h2h_points"] += 3
            away["losses"] += 1
        elif away_goals > home_goals:
            away["wins"] += 1
            away["points"] += 3
            away["h2h_points"] += 3
            home["losses"] += 1
        else:
            home["draws"] += 1
            away["draws"] += 1
            home["points"] += 1
            away["points"] += 1
            home["h2h_points"] += 1
            away["h2h_points"] += 1
        home["h2h_gd"] += home_goals - away_goals
        away["h2h_gd"] += away_goals - home_goals
    return sorted(
        table.values(),
        key=lambda team: (
            -team["points"],
            -(team["gf"] - team["ga"]),
            -team["gf"],
            -team["h2h_points"],
            -team["h2h_gd"],
            team["team_name"].casefold(),
        ),
    )


def get_championship(
    guild_id: int | str,
    *,
    now: datetime | None = None,
    database_path: str | Path = DATABASE_PATH,
) -> dict | None:
    date = competition_date(now)
    with connect(database_path) as connection:
        championship = connection.execute(
            """
            SELECT * FROM championships
            WHERE guild_id = ? AND competition_date = ?
            """,
            (str(guild_id), date),
        ).fetchone()
        if championship is None:
            return None
        entries = [
            dict(row)
            for row in connection.execute(
                """
                SELECT discord_user_id, team_name FROM championship_entries
                WHERE championship_id = ?
                """,
                (championship["id"],),
            )
        ]
        fixtures = [
            dict(row)
            for row in connection.execute(
                """
                SELECT * FROM championship_fixtures
                WHERE championship_id = ?
                ORDER BY is_final, match_order
                """,
                (championship["id"],),
            )
        ]
    played = [fixture for fixture in fixtures if fixture["home_goals"] is not None]
    return {
        **dict(championship),
        "entries": entries,
        "fixtures": fixtures,
        "standings": _standings(entries, [f for f in played if not f["is_final"]]),
    }


def play_championship(
    guild_id: int | str,
    *,
    now: datetime | None = None,
    rng: random.Random | None = None,
    database_path: str | Path = DATABASE_PATH,
) -> dict:
    date = competition_date(now)
    generator = rng or random.SystemRandom()
    with connect(database_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        championship = connection.execute(
            """
            SELECT * FROM championships
            WHERE guild_id = ? AND competition_date = ?
            """,
            (str(guild_id), date),
        ).fetchone()
        if championship is None:
            raise ChampionshipError("Ainda não há inscrições para hoje.")
        if championship["status"] == "completed":
            raise ChampionshipError("O campeonato de hoje já terminou.")
        entries = [
            dict(row)
            for row in connection.execute(
                """
                SELECT discord_user_id, team_name FROM championship_entries
                WHERE championship_id = ? ORDER BY team_name COLLATE NOCASE
                """,
                (championship["id"],),
            )
        ]
        if len(entries) < 2:
            raise ChampionshipError("São necessários pelo menos dois times inscritos.")
        for entry in entries:
            try:
                get_valid_lineup(entry["discord_user_id"], database_path)
            except TeamError as error:
                raise ChampionshipError(
                    f"{entry['team_name']} não tem uma escalação válida: {error}"
                ) from error
        connection.execute(
            "UPDATE championships SET status = 'playing' WHERE id = ?",
            (championship["id"],),
        )
        fixture_count = connection.execute(
            """
            SELECT COUNT(*) FROM championship_fixtures
            WHERE championship_id = ? AND is_final = 0
            """,
            (championship["id"],),
        ).fetchone()[0]
        if fixture_count == 0:
            match_order = 0
            for index, home in enumerate(entries):
                for away in entries[index + 1 :]:
                    match_order += 1
                    connection.execute(
                        """
                        INSERT OR IGNORE INTO championship_fixtures (
                            championship_id, round_no, match_order,
                            home_user_id, away_user_id, is_final
                        ) VALUES (?, 1, ?, ?, ?, 0)
                        """,
                        (
                            championship["id"],
                            match_order,
                            home["discord_user_id"],
                            away["discord_user_id"],
                        ),
                    )
        connection.commit()

    with connect(database_path) as connection:
        fixtures = [
            dict(row)
            for row in connection.execute(
                """
                SELECT * FROM championship_fixtures
                WHERE championship_id = ? AND is_final = 0
                  AND home_goals IS NULL
                ORDER BY match_order
                """,
                (championship["id"],),
            )
        ]
        entries = [
            dict(row)
            for row in connection.execute(
                """
                SELECT discord_user_id, team_name FROM championship_entries
                WHERE championship_id = ?
                """,
                (championship["id"],),
            )
        ]
    for fixture in fixtures:
        _play_fixture(fixture, generator, database_path)

    with connect(database_path) as connection:
        group_fixtures = [
            dict(row)
            for row in connection.execute(
                """
                SELECT * FROM championship_fixtures
                WHERE championship_id = ? AND is_final = 0
                ORDER BY match_order
                """,
                (championship["id"],),
            )
        ]
    top_two = _standings(entries, group_fixtures)[:2]
    with connect(database_path) as connection:
        final_exists = connection.execute(
            """
            SELECT 1 FROM championship_fixtures
            WHERE championship_id = ? AND is_final = 1
            """,
            (championship["id"],),
        ).fetchone()
        if not final_exists:
            connection.execute(
                """
                INSERT OR IGNORE INTO championship_fixtures (
                    championship_id, round_no, match_order, home_user_id,
                    away_user_id, is_final
                ) VALUES (?, 2, 999999, ?, ?, 1)
                """,
                (
                    championship["id"],
                    top_two[0]["discord_user_id"],
                    top_two[1]["discord_user_id"],
                ),
            )
            connection.commit()
        final_fixture = dict(
            connection.execute(
                """
            SELECT * FROM championship_fixtures
            WHERE championship_id = ? AND is_final = 1
            """,
                (championship["id"],),
            ).fetchone()
        )
    prize_paid = False
    if final_fixture["home_goals"] is None:
        final_fixture = _play_fixture(final_fixture, generator, database_path, final=True)
        if final_fixture["winner_user_id"]:
            with connect(database_path) as connection:
                connection.execute(
                    """
                    UPDATE game_users SET memilets = memilets + ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE discord_user_id = ?
                    """,
                    (CHAMPION_PRIZE, final_fixture["winner_user_id"]),
                )
            prize_paid = True
    with connect(database_path) as connection:
        connection.execute(
            """
            UPDATE championships SET status = 'completed', completed_at = ?
            WHERE id = ?
            """,
            (_utc_now(now).isoformat(timespec="seconds"), championship["id"]),
        )
    result = get_championship(
        guild_id,
        now=now,
        database_path=database_path,
    )
    if result is None:
        raise ChampionshipError("O campeonato desapareceu durante a execução.")
    result["winner_user_id"] = final_fixture["winner_user_id"]
    result["prize"] = CHAMPION_PRIZE if prize_paid else 0
    return result


def _play_fixture(
    fixture: dict,
    rng: random.Random,
    database_path: str | Path,
    *,
    final: bool = False,
) -> dict:
    home_team, home_players = get_valid_lineup(
        fixture["home_user_id"],
        database_path,
    )
    away_team, away_players = get_valid_lineup(
        fixture["away_user_id"],
        database_path,
    )
    result = simulate_match(
        home_team["team_name"],
        home_players,
        away_team["team_name"],
        away_players,
        home_style=home_team["style"],
        away_style=away_team["style"],
        rng=rng,
    )
    winner_id = None
    extra = {}
    if final and result.home_goals == result.away_goals:
        kicks = penalty_shootout_kicks(
            home_players,
            away_players,
            home_taker_id=home_team["penalty_player_id"],
            away_taker_id=away_team["penalty_player_id"],
            rng=rng,
        )
        home_penalties, away_penalties = kicks[-1].home_score, kicks[-1].away_score
        extra["penalties"] = [home_penalties, away_penalties]
        extra["penalty_kicks"] = [asdict(kick) for kick in kicks]
        winner_id = (
            fixture["home_user_id"] if home_penalties > away_penalties else fixture["away_user_id"]
        )
    elif final:
        winner_id = (
            fixture["home_user_id"]
            if result.home_goals > result.away_goals
            else fixture["away_user_id"]
        )
    events = result.to_dict()
    events["home_lineup"] = [int(player["id"]) for player in home_players]
    events["away_lineup"] = [int(player["id"]) for player in away_players]
    events["player_names"] = {
        str(player["id"]): str(player.get("display_name") or player["name"])
        for player in (*home_players, *away_players)
    }
    events.update(extra)
    if final:
        home_won = winner_id == fixture["home_user_id"]
        away_won = winner_id == fixture["away_user_id"]
    else:
        home_won = result.home_goals > result.away_goals
        away_won = result.away_goals > result.home_goals
    with connect(database_path) as connection:
        # Andar 5 da torre: cada jogo oficial enche a bilheteria dos dois times.
        events["box_office"] = {
            "home": add_box_office(connection, str(fixture["home_user_id"]), home_won),
            "away": add_box_office(connection, str(fixture["away_user_id"]), away_won),
        }
        connection.execute(
            """
            UPDATE championship_fixtures
            SET home_goals = ?, away_goals = ?, events_json = ?,
                winner_user_id = ?, played_at = ?
            WHERE id = ? AND home_goals IS NULL
            """,
            (
                result.home_goals,
                result.away_goals,
                json.dumps(events, ensure_ascii=False),
                winner_id,
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
                fixture["id"],
            ),
        )
        row = connection.execute(
            "SELECT * FROM championship_fixtures WHERE id = ?",
            (fixture["id"],),
        ).fetchone()
    return dict(row)


def _utc_now(now: datetime | None) -> datetime:
    value = now or datetime.now(timezone.utc)
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("now deve incluir um fuso horário.")
    return value.astimezone(timezone.utc)
