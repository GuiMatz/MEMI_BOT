"""Temporada mensal: soma os campeonatos diários do mês numa tabela só.

Cada jogo vale 3 pontos na vitória e 1 no empate (finais decididas nos pênaltis contam
como vitória de quem levou). Desempate: pontos, títulos, saldo e gols marcados.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.clock import BRASILIA
from src.db import DATABASE_PATH, connect

MONTHS = (
    "janeiro",
    "fevereiro",
    "março",
    "abril",
    "maio",
    "junho",
    "julho",
    "agosto",
    "setembro",
    "outubro",
    "novembro",
    "dezembro",
)


def current_month(now: datetime | None = None) -> str:
    return (now or datetime.now(timezone.utc)).astimezone(BRASILIA).strftime("%Y-%m")


def month_label(month: str) -> str:
    year, number = month.split("-")
    return f"{MONTHS[int(number) - 1].capitalize()} de {year}"


def parse_month(text: str | None, now: datetime | None = None) -> str:
    """Aceita `10`, `10/2026`, `2026-10` ou o nome do mês; sem texto, o mês atual."""
    if not text:
        return current_month(now)
    value = text.strip().casefold()
    year = int(current_month(now)[:4])
    if value in MONTHS:
        return f"{year}-{MONTHS.index(value) + 1:02d}"
    for separator in ("/", "-"):
        if separator in value:
            first, second = value.split(separator, 1)
            if len(first) == 4:
                return f"{int(first)}-{int(second):02d}"
            return f"{int(second)}-{int(first):02d}"
    if value.isdigit() and 1 <= int(value) <= 12:
        return f"{year}-{int(value):02d}"
    raise ValueError("Mês inválido. Use, por exemplo, `mm!temporada 10/2026`.")


def season_table(
    guild_id: int | str, month: str, database_path: str | Path = DATABASE_PATH
) -> list[dict[str, Any]]:
    with connect(database_path) as connection:
        fixtures = [
            dict(row)
            for row in connection.execute(
                """
                SELECT f.home_user_id, f.away_user_id, f.home_goals, f.away_goals,
                       f.is_final, f.winner_user_id
                FROM championship_fixtures AS f
                JOIN championships AS c ON c.id = f.championship_id
                WHERE c.guild_id = ? AND c.status = 'completed'
                  AND c.competition_date LIKE ? AND f.home_goals IS NOT NULL
                """,
                (str(guild_id), f"{month}-%"),
            )
        ]
        teams = {
            row["discord_user_id"]: dict(row)
            for row in connection.execute(
                "SELECT discord_user_id, team_name, color, logo_path FROM game_teams"
            )
        }
    table: dict[str, dict[str, Any]] = {}

    def entry(user_id: str) -> dict[str, Any]:
        if user_id not in table:
            team = teams.get(user_id, {})
            table[user_id] = {
                "discord_user_id": user_id,
                "team_name": team.get("team_name") or "Time",
                "color": team.get("color") or "#2E8B57",
                "logo_path": team.get("logo_path"),
                "points": 0,
                "played": 0,
                "wins": 0,
                "draws": 0,
                "losses": 0,
                "gf": 0,
                "ga": 0,
                "titles": 0,
            }
        return table[user_id]

    for fixture in fixtures:
        home, away = entry(fixture["home_user_id"]), entry(fixture["away_user_id"])
        home_goals, away_goals = int(fixture["home_goals"]), int(fixture["away_goals"])
        for team, scored, conceded in (
            (home, home_goals, away_goals),
            (away, away_goals, home_goals),
        ):
            team["played"] += 1
            team["gf"] += scored
            team["ga"] += conceded
        winner = fixture["winner_user_id"] if fixture["is_final"] else None
        if winner is None and home_goals != away_goals:
            winner = fixture["home_user_id"] if home_goals > away_goals else fixture["away_user_id"]
        if winner is None:
            for team in (home, away):
                team["draws"] += 1
                team["points"] += 1
        else:
            loser = (
                fixture["away_user_id"]
                if winner == fixture["home_user_id"]
                else fixture["home_user_id"]
            )
            entry(winner)["wins"] += 1
            entry(winner)["points"] += 3
            entry(loser)["losses"] += 1
        if fixture["is_final"] and fixture["winner_user_id"]:
            entry(fixture["winner_user_id"])["titles"] += 1
    return sorted(
        table.values(),
        key=lambda team: (
            -team["points"],
            -team["titles"],
            -(team["gf"] - team["ga"]),
            -team["gf"],
            team["team_name"].casefold(),
        ),
    )
