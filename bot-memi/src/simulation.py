"""Motor determinístico de partidas de futebol do MEMI, narrado minuto a minuto."""

from __future__ import annotations

import math
import random
from dataclasses import asdict, dataclass, field
from typing import Any, Sequence

from src.teams import POSITION_GROUPS, TeamError

BASE_XG = 1.25
MIN_XG = 0.20
MAX_XG = 3.50
STYLE_FACTORS = {
    "balanced": (1.00, 1.00),
    "attack": (1.10, 1.06),
    "counter": (1.03, 0.96),
}
# Parte das finalizações perigosas que vira gol; o resto é defesa, trave ou fora.
CHANCE_CONVERSION = 0.32
YELLOW_CARD_CHANCE = 0.018  # por minuto e por time
HALF_LENGTH = 45


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
    stoppage: int = 0


@dataclass(frozen=True)
class MatchEvent:
    """Um lance narrado. `minute` fica em 1-90; acréscimos vão em `stoppage`."""

    minute: int
    kind: str
    text: str
    side: str | None = None
    home_goals: int = 0
    away_goals: int = 0
    stoppage: int = 0
    player: str | None = None
    # Goleiro que defendeu ou quem deu a assistência.
    detail: str | None = None

    @property
    def clock(self) -> str:
        return f"{self.minute}+{self.stoppage}'" if self.stoppage else f"{self.minute}'"


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
    events: tuple[MatchEvent, ...] = field(default=())

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["goals"] = [asdict(goal) for goal in self.goals]
        result["events"] = [asdict(event) for event in self.events]
        return result


@dataclass(frozen=True)
class PenaltyKick:
    side: str
    kicker_name: str
    scored: bool
    home_score: int
    away_score: int


def _effective_overall(player: dict[str, Any]) -> int:
    value = player.get("effective_overall", player.get("overall"))
    if value is None or not 1 <= int(value) <= 100:
        raise TeamError("Todos os titulares precisam ter overall entre 1 e 100.")
    return int(value)


def _name(player: dict[str, Any]) -> str:
    return str(player.get("display_name") or player["name"])


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
    averages = {group: sum(values) / len(values) for group, values in totals.items()}
    attack = 0.60 * averages["A"] + 0.40 * averages["M"]
    resistance = 0.55 * averages["D"] + 0.30 * averages["M"] + 0.15 * averages["G"]
    return TeamStrength(attack, resistance, averages)


def expected_goals(attack: float, resistance: float) -> float:
    difference = attack - resistance
    return min(MAX_XG, max(MIN_XG, BASE_XG * math.exp(0.018 * difference)))


def _scorer(players: Sequence[dict[str, Any]], rng: random.Random) -> dict[str, Any]:
    weights = []
    for player in players:
        position = player["position"]
        position_weight = (
            3.0
            if POSITION_GROUPS[position] == "A"
            else (1.0 if POSITION_GROUPS[position] == "M" else 0.15)
        )
        weights.append(_effective_overall(player) * position_weight)
    return rng.choices(players, weights=weights, k=1)[0]


def _assistant(
    players: Sequence[dict[str, Any]],
    scorer: dict[str, Any],
    rng: random.Random,
) -> dict[str, Any] | None:
    options = [
        player
        for player in players
        if player is not scorer and POSITION_GROUPS[player["position"]] != "G"
    ]
    if not options or rng.random() < 0.25:
        return None
    weights = [
        (
            2.0
            if POSITION_GROUPS[player["position"]] == "M"
            else (1.5 if POSITION_GROUPS[player["position"]] == "A" else 0.6)
        )
        for player in options
    ]
    return rng.choices(options, weights=weights, k=1)[0]


def _goalkeeper(players: Sequence[dict[str, Any]]) -> dict[str, Any]:
    return next(player for player in players if player["position"] == "GOL")


GOAL_LINES = (
    "⚽ **GOOOL do {team}!** {scorer} bate firme e estufa a rede!",
    "⚽ **GOOOL!** {scorer} aparece livre na área e marca para o {team}!",
    "⚽ **É GOL!** Que pintura de {scorer}! {team} comemora.",
    "⚽ **GOOOL do {team}!** {scorer} tira do goleiro e manda para o fundo do gol!",
    "⚽ **GOL do {team}!** {scorer} de cabeça, sem chances para a defesa!",
)
SAVE_LINES = (
    "🧤 {shooter} arrisca de fora e {keeper} faz grande defesa!",
    "🧤 Que defesa de {keeper}! Impediu o gol de {shooter}.",
    "🧤 {shooter} cara a cara… {keeper} fecha o ângulo e salva!",
)
MISS_LINES = (
    "😬 {shooter} finaliza e a bola passa raspando a trave!",
    "😩 {shooter} isolou! Era uma chance clara para o {team}.",
    "💨 {shooter} chuta cruzado, mas a bola sai pela linha de fundo.",
)
POST_LINES = (
    "🥅 NA TRAVE! {shooter} quase marca para o {team}!",
    "🥅 Bola no travessão! {shooter} acertou a madeira!",
)


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

    sides = {
        "home": (home_name, home_players, home_xg / (90 * CHANCE_CONVERSION)),
        "away": (away_name, away_players, away_xg / (90 * CHANCE_CONVERSION)),
    }
    score = {"home": 0, "away": 0}
    goals: list[Goal] = []
    events: list[MatchEvent] = []
    booked: set[tuple[str, int]] = set()

    def add(
        minute: int,
        stoppage: int,
        kind: str,
        text: str,
        side: str | None = None,
        player: str | None = None,
        detail: str | None = None,
    ) -> None:
        events.append(
            MatchEvent(
                minute, kind, text, side, score["home"], score["away"], stoppage, player, detail
            )
        )

    add(1, 0, "kickoff", f"🟢 Rola a bola! {home_name} × {away_name}.")
    first_stoppage = generator.randint(1, 3)
    second_stoppage = generator.randint(2, 5)
    timeline = [(minute, 0) for minute in range(1, HALF_LENGTH + 1)]
    timeline += [(HALF_LENGTH, extra) for extra in range(1, first_stoppage + 1)]
    timeline += [("half", 0)]
    timeline += [(minute, 0) for minute in range(HALF_LENGTH + 1, 91)]
    timeline += [(90, extra) for extra in range(1, second_stoppage + 1)]

    for minute, stoppage in timeline:
        if minute == "half":
            add(
                HALF_LENGTH,
                first_stoppage,
                "halftime",
                f"⏸️ **Intervalo:** {home_name} {score['home']} × {score['away']} {away_name}.",
            )
            add(HALF_LENGTH + 1, 0, "second_half", "🔁 Bola rolando para o segundo tempo!")
            continue
        for side in ("home", "away"):
            team_name, players, chance = sides[side]
            opponent = sides["away" if side == "home" else "home"][1]
            if generator.random() < chance:
                shooter = _scorer(players, generator)
                if generator.random() < CHANCE_CONVERSION:
                    score[side] += 1
                    goals.append(Goal(minute, side, int(shooter["id"]), _name(shooter), stoppage))
                    assistant = _assistant(players, shooter, generator)
                    text = generator.choice(GOAL_LINES).format(
                        team=team_name, scorer=_name(shooter)
                    )
                    if assistant is not None:
                        text += f" Assistência de {_name(assistant)}."
                    add(
                        minute,
                        stoppage,
                        "goal",
                        text,
                        side,
                        _name(shooter),
                        _name(assistant) if assistant is not None else None,
                    )
                else:
                    roll = generator.random()
                    lines, kind = (
                        (SAVE_LINES, "save")
                        if roll < 0.5
                        else (POST_LINES, "post") if roll < 0.62 else (MISS_LINES, "miss")
                    )
                    keeper = _name(_goalkeeper(opponent))
                    add(
                        minute,
                        stoppage,
                        kind,
                        generator.choice(lines).format(
                            shooter=_name(shooter), keeper=keeper, team=team_name
                        ),
                        side,
                        _name(shooter),
                        keeper if kind == "save" else None,
                    )
            if generator.random() < YELLOW_CARD_CHANCE:
                fouler = generator.choice(
                    [player for player in players if player["position"] != "GOL"]
                )
                key = (side, int(fouler["id"]))
                if key in booked:
                    continue
                booked.add(key)
                add(
                    minute,
                    stoppage,
                    "card",
                    f"🟨 Cartão amarelo para {_name(fouler)} ({team_name}).",
                    side,
                    _name(fouler),
                )
    add(
        90,
        second_stoppage,
        "fulltime",
        f"🏁 **Fim de jogo:** {home_name} {score['home']} × {score['away']} {away_name}.",
    )
    return MatchResult(
        home_name=home_name,
        away_name=away_name,
        home_goals=score["home"],
        away_goals=score["away"],
        home_xg=home_xg,
        away_xg=away_xg,
        home_strength=home_strength,
        away_strength=away_strength,
        goals=tuple(goals),
        events=tuple(events),
    )


def penalty_shootout_kicks(
    home_players: Sequence[dict[str, Any]],
    away_players: Sequence[dict[str, Any]],
    *,
    home_taker_id: int | None = None,
    away_taker_id: int | None = None,
    rng: random.Random | None = None,
) -> list[PenaltyKick]:
    """Disputa completa de pênaltis, cobrança a cobrança, até haver vencedor."""
    generator = rng or random.SystemRandom()
    home_strength = calculate_team_strength(home_players).attack
    away_strength = calculate_team_strength(away_players).attack
    home_probability = min(0.85, max(0.65, 0.75 + (home_strength - away_strength) * 0.002))
    away_probability = min(0.85, max(0.65, 0.75 + (away_strength - home_strength) * 0.002))
    scores = [0, 0]
    kicks: list[PenaltyKick] = []
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

    def kick(side: int, kicker: dict[str, Any]) -> None:
        probability = home_probability if side == 0 else away_probability
        probability = min(0.90, max(0.60, probability + (_effective_overall(kicker) - 75) * 0.001))
        scored = generator.random() < probability
        if scored:
            scores[side] += 1
        kicks.append(
            PenaltyKick(
                "home" if side == 0 else "away", _name(kicker), scored, scores[0], scores[1]
            )
        )

    for round_number in range(5):
        for side in (0, 1):
            kick(side, kickers[side][round_number % len(kickers[side])])
            home_left = 5 - round_number - 1
            away_left = 5 - round_number - (1 if side == 1 else 0)
            if scores[0] > scores[1] + away_left or scores[1] > scores[0] + home_left:
                return kicks
    round_number = 5
    while scores[0] == scores[1]:
        for side in (0, 1):
            kick(side, kickers[side][round_number % len(kickers[side])])
        round_number += 1
    return kicks


def penalty_shootout(
    home_players: Sequence[dict[str, Any]],
    away_players: Sequence[dict[str, Any]],
    *,
    home_taker_id: int | None = None,
    away_taker_id: int | None = None,
    rng: random.Random | None = None,
) -> tuple[int, int]:
    kicks = penalty_shootout_kicks(
        home_players,
        away_players,
        home_taker_id=home_taker_id,
        away_taker_id=away_taker_id,
        rng=rng,
    )
    return kicks[-1].home_score, kicks[-1].away_score


def penalty_events(
    kicks: Sequence[PenaltyKick | dict[str, Any]],
    home_name: str,
    away_name: str,
) -> list[dict[str, Any]]:
    """Converte as cobranças em lances narráveis, no mesmo formato de `MatchEvent`."""
    events = [
        {
            "minute": 90,
            "stoppage": 0,
            "kind": "penalties",
            "side": None,
            "home_goals": 0,
            "away_goals": 0,
            "text": "🎯 **Empate! A decisão vai para os pênaltis.**",
        }
    ]
    for kick in kicks:
        data = asdict(kick) if isinstance(kick, PenaltyKick) else kick
        team = home_name if data["side"] == "home" else away_name
        text = (
            f"✅ {data['kicker_name']} ({team}) converte!"
            if data["scored"]
            else f"❌ {data['kicker_name']} ({team}) perde a cobrança!"
        )
        events.append(
            {
                "minute": 90,
                "stoppage": 0,
                "kind": "penalty",
                "side": data["side"],
                "player": data["kicker_name"],
                "scored": data["scored"],
                "home_goals": data["home_score"],
                "away_goals": data["away_score"],
                "text": f"{text} Pênaltis: {data['home_score']} × {data['away_score']}",
            }
        )
    return events
