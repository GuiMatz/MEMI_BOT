"""Comparação de dois times lado a lado (`mm!comparar @pessoa`).

Junta o que o bot já sabe de cada time (força por setor do simulador, escalação, elenco,
jogos entre os dois) e um prognóstico: a partida simulada `FORECAST_SIMULATIONS` vezes,
com quem pediu jogando em casa.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

from src.db import DATABASE_PATH, connect
from src.lineup_image import render_lineup
from src.positions import slot_rating
from src.ratings import GAME_OVERALL_SQL
from src.simulation import TeamProfile, simulate_match, team_profile
from src.team_stats import compute_stats, filter_records, match_records
from src.teams import TeamError, get_lineup, get_team, get_valid_lineup
from src.transactions import sale_value

FORECAST_SIMULATIONS = 500


@dataclass
class TeamSnapshot:
    user_id: int
    team: dict[str, Any]
    players: list[dict[str, Any]]
    complete: bool
    profile: TeamProfile | None
    strength: int
    best: dict[str, Any] | None
    squad_size: int
    squad_value: int
    form: list[str]

    @property
    def name(self) -> str:
        return self.team["team_name"]


def snapshot(discord_user_id: int, database_path: str | Path = DATABASE_PATH) -> TeamSnapshot:
    """Tudo o que a comparação mostra de um time; `TeamError` se a pessoa não tem time."""
    team = get_team(discord_user_id, database_path)
    if team is None:
        raise TeamError("Esse time ainda não foi criado (`mm!time`).")
    try:
        team, players = get_valid_lineup(discord_user_id, database_path)
        complete = True
    except TeamError:
        players = get_lineup(discord_user_id, database_path)
        complete = False
    profile = team_profile(players, team) if complete else None
    ratings = [
        slot_rating(p["effective_overall"], p["natural_position"], p["position"])
        for p in players
        if p.get("effective_overall")
    ]
    with connect(database_path) as connection:
        overalls = [
            int(row["overall"] or 0)
            for row in connection.execute(
                f"""
                SELECT {GAME_OVERALL_SQL} AS overall FROM player_ownership AS o
                JOIN players AS p ON p.id = o.player_id WHERE o.discord_user_id = ?
                """,
                (str(discord_user_id),),
            )
        ]
    stats = compute_stats(match_records(discord_user_id, database_path))
    return TeamSnapshot(
        discord_user_id,
        team,
        players,
        complete,
        profile,
        round(sum(ratings) / 11) if ratings else 0,
        max(players, key=lambda p: p.get("effective_overall") or 0) if players else None,
        len(overalls),
        sum(sale_value(overall) for overall in overalls if overall),
        stats.form[:5],
    )


def head_to_head(
    first_id: int, second_id: int, database_path: str | Path = DATABASE_PATH
) -> tuple[int, int, int, int, int]:
    """(vitórias, empates, derrotas, gols pró, gols contra) do primeiro contra o segundo."""
    records = filter_records(match_records(first_id, database_path), opponent=str(second_id))
    stats = compute_stats(records)
    return stats.wins, stats.draws, stats.losses, stats.goals_for, stats.goals_against


def forecast(
    home: TeamSnapshot, away: TeamSnapshot, simulations: int = FORECAST_SIMULATIONS, rng=None
) -> tuple[float, float, float] | None:
    """Chance de vitória, empate e derrota de `home` (em casa) nos 90 minutos."""
    if not (home.complete and away.complete):
        return None
    generator = rng or random.Random()
    wins = draws = 0
    for _ in range(simulations):
        result = simulate_match(
            home.name,
            home.players,
            away.name,
            away.players,
            home_style=home.team["style"],
            away_style=away.team["style"],
            home_tactics=home.team,
            away_tactics=away.team,
            home_advantage=True,
            rng=generator,
        )
        if result.home_goals > result.away_goals:
            wins += 1
        elif result.home_goals == result.away_goals:
            draws += 1
    return wins / simulations, draws / simulations, (simulations - wins - draws) / simulations


def compare_rows(first: TeamSnapshot, second: TeamSnapshot) -> list[tuple[str, Any, Any]]:
    """Linhas (rótulo, valor do primeiro, valor do segundo) da tabela de comparação."""
    rows: list[tuple[str, Any, Any]] = [("Força", first.strength, second.strength)]
    if first.profile and second.profile:
        for label, attribute in (
            ("Ataque", "attack"),
            ("Defesa", "defense"),
            ("Meio", "control"),
            ("Goleiro", "keeper"),
        ):
            rows.append(
                (
                    label,
                    round(getattr(first.profile, attribute)),
                    round(getattr(second.profile, attribute)),
                )
            )
    rows.append(("Elenco", first.squad_size, second.squad_size))
    return rows


def compare_image(first: TeamSnapshot, second: TeamSnapshot) -> BytesIO:
    """As duas escalações lado a lado, com um "VS" no meio."""
    left = Image.open(render_lineup(first.team, first.players)).convert("RGBA")
    right = Image.open(render_lineup(second.team, second.players)).convert("RGBA")
    height = max(left.height, right.height)
    gap = 24
    canvas = Image.new("RGBA", (left.width + right.width + gap, height), (12, 15, 22, 255))
    canvas.alpha_composite(left, (0, (height - left.height) // 2))
    canvas.alpha_composite(right, (left.width + gap, (height - right.height) // 2))
    draw = ImageDraw.Draw(canvas)
    center = (left.width + gap / 2, height / 2)
    radius = 46
    draw.ellipse(
        (center[0] - radius, center[1] - radius, center[0] + radius, center[1] + radius),
        fill=(12, 15, 22, 255),
        outline=(230, 181, 74, 255),
        width=5,
    )
    from src.visual import font

    draw.text(center, "VS", font=font(40, True), fill=(230, 181, 74, 255), anchor="mm")
    output = BytesIO()
    canvas.convert("RGB").save(output, format="PNG", optimize=True)
    output.seek(0)
    return output
