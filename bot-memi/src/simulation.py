"""Motor determinístico de partidas de futebol do MEMI."""

from __future__ import annotations

import math
import random
from dataclasses import asdict, dataclass
from typing import Any, Sequence

from src.teams import POSITION_GROUPS, STYLE_LABELS, TeamError

BASE_XG = 1.25
MIN_XG = 0.20
MAX_XG = 3.50
STYLE_FACTORS = {
    "balanced": (1.00, 1.00),
    "attack": (1.10, 1.06),
    "counter": (1.03, 0.96),
}


@dataclass(frozen=True)
class TeamStrength:
    attack: float
    resistance: float
    groups: dict[str, float]


@dataclass(frozen=True)
class Goal:
    minute: int
    side: str
    scorer_id: int
    scorer_name: str


@dataclass(frozen=True)
class MatchResult:
    home_name: str
    away_name: str
    home_goals: int
    away_goals: int
    home_xg: float
    away_xg: float
    home_strength: TeamStrength
    away_strength: TeamStrength
    goals: tuple[Goal, ...]

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["goals"] = [asdict(goal) for goal in self.goals]
        return result


def _effective_overall(player: dict[str, Any]) -> int:
    value = player.get("effective_overall", player.get("overall"))
    if value is None or not 1 <= int(value) <= 100:
        raise TeamError("Todos os titulares precisam ter overall entre 1 e 100.")
    return int(value)


def calculate_team_strength(players: Sequence[dict[str, Any]]) -> TeamStrength:
    if len(players) != 11:
        raise TeamError("A simulação exige exatamente 11 titulares.")
    totals = {"G": [], "D": [], "M": [], "A": []}
    for player in players:
        position = player.get("position")
        group = POSITION_GROUPS.get(position)
        if group is None:
            raise TeamError(f"Posição inválida na escalação: {position}.")
        totals[group].append(_effective_overall(player))
    if any(not values for values in totals.values()):
        raise TeamError("A escalação precisa preencher goleiro, defesa, meio e ataque.")
    averages = {
        group: sum(values) / len(values) for group, values in totals.items()
    }
    attack = 0.60 * averages["A"] + 0.40 * averages["M"]
    resistance = (
        0.55 * averages["D"] + 0.30 * averages["M"] + 0.15 * averages["G"]
    )
    return TeamStrength(attack, resistance, averages)


def expected_goals(attack: float, resistance: float) -> float:
    difference = attack - resistance
    return min(MAX_XG, max(MIN_XG, BASE_XG * math.exp(0.018 * difference)))


def _poisson(lam: float, rng: random.Random) -> int:
    threshold = math.exp(-lam)
    product = 1.0
    goals = 0
    while product > threshold:
        goals += 1
        product *= rng.random()
    return goals - 1


def _scorer(players: Sequence[dict[str, Any]], rng: random.Random) -> dict[str, Any]:
    weights = []
    for player in players:
        position = player["position"]
        position_weight = 3.0 if POSITION_GROUPS[position] == "A" else (
            1.0 if POSITION_GROUPS[position] == "M" else 0.15
        )
        weights.append(_effective_overall(player) * position_weight)
    return rng.choices(players, weights=weights, k=1)[0]


def simulate_match(
    home_name: str,
    home_players: Sequence[dict[str, Any]],
    away_name: str,
    away_players: Sequence[dict[str, Any]],
    *,
    home_style: str = "balanced",
    away_style: str = "balanced",
    rng: random.Random | None = None,
) -> MatchResult:
    generator = rng or random.SystemRandom()
    if home_style not in STYLE_FACTORS or away_style not in STYLE_FACTORS:
        raise TeamError("Estilo de time inválido.")
    home_strength = calculate_team_strength(home_players)
    away_strength = calculate_team_strength(away_players)
    home_xg = expected_goals(home_strength.attack, away_strength.resistance)
    away_xg = expected_goals(away_strength.attack, home_strength.resistance)
    home_own, home_opponent = STYLE_FACTORS[home_style]
    away_own, away_opponent = STYLE_FACTORS[away_style]
    home_xg = min(MAX_XG, max(MIN_XG, home_xg * home_own * away_opponent))
    away_xg = min(MAX_XG, max(MIN_XG, away_xg * away_own * home_opponent))

    home_goal_count = _poisson(home_xg, generator)
    away_goal_count = _poisson(away_xg, generator)
    goals: list[Goal] = []
    for side, players, count in (
        ("home", home_players, home_goal_count),
        ("away", away_players, away_goal_count),
    ):
        for _ in range(count):
            scorer = _scorer(players, generator)
            goals.append(
                Goal(
                    minute=generator.randint(1, 90),
                    side=side,
                    scorer_id=int(scorer["id"]),
                    scorer_name=str(scorer.get("display_name") or scorer["name"]),
                )
            )
    goals.sort(key=lambda goal: goal.minute)
    return MatchResult(
        home_name=home_name,
        away_name=away_name,
        home_goals=home_goal_count,
        away_goals=away_goal_count,
        home_xg=home_xg,
        away_xg=away_xg,
        home_strength=home_strength,
        away_strength=away_strength,
        goals=tuple(goals),
    )


def penalty_shootout(
    home_players: Sequence[dict[str, Any]],
    away_players: Sequence[dict[str, Any]],
    *,
    home_taker_id: int | None = None,
    away_taker_id: int | None = None,
    rng: random.Random | None = None,
) -> tuple[int, int]:
    generator = rng or random.SystemRandom()
    home_strength = calculate_team_strength(home_players).attack
    away_strength = calculate_team_strength(away_players).attack
    home_probability = min(0.85, max(0.65, 0.75 + (home_strength - away_strength) * 0.002))
    away_probability = min(0.85, max(0.65, 0.75 + (away_strength - home_strength) * 0.002))
    scores = [0, 0]
    kickers = []
    for players, taker_id in (
        (home_players, home_taker_id),
        (away_players, away_taker_id),
    ):
        sorted_players = sorted(
            players,
            key=lambda player: _effective_overall(player),
            reverse=True,
        )
        if taker_id is not None:
            selected = next(
                (player for player in sorted_players if player["id"] == taker_id),
                None,
            )
            if selected is not None:
                sorted_players = [selected] + [
                    player for player in sorted_players if player["id"] != taker_id
                ]
        kickers.append(sorted_players)
    for kick in range(5):
        for side in (0, 1):
            kicker = kickers[side][kick % len(kickers[side])]
            probability = home_probability if side == 0 else away_probability
            probability = min(0.90, max(0.60, probability + (_effective_overall(kicker) - 75) * 0.001))
            if generator.random() < probability:
                scores[side] += 1
        remaining = 4 - kick
        if scores[0] > scores[1] + remaining:
            return scores[0], scores[1]
        if scores[1] > scores[0] + remaining:
            return scores[0], scores[1]
    while scores[0] == scores[1]:
        for side in (0, 1):
            kicker = kickers[side][generator.randrange(len(kickers[side]))]
            probability = home_probability if side == 0 else away_probability
            probability = min(0.90, max(0.60, probability + (_effective_overall(kicker) - 75) * 0.001))
            if generator.random() < probability:
                scores[side] += 1
    return scores[0], scores[1]
