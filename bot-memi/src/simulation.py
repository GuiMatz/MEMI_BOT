"""Motor de partidas do FUT.AE, narrado minuto a minuto, com peso de cada jogador.

Cada titular conta pelo que rende **na vaga** (overall com bolas × rendimento da
posição, `src.positions`). A partir disso:

- **Ataque e defesa por jogador.** Cada vaga contribui de um jeito (`ATTACK_WEIGHT`,
  `DEFENSE_WEIGHT`): o centroavante pesa muito no ataque e quase nada na defesa, o
  zagueiro o contrário. O índice do setor é a média ponderada dos jogadores e o volume
  é a soma dos pesos, então a formação importa (3-4-3 ataca com mais gente; 5-3-2
  defende com mais gente).
- **Meio-campo.** `CONTROL_WEIGHT` mede quem domina o jogo; o capitão soma um pouco.
- **Chances.** Cada time cria chances conforme o próprio ataque contra a defesa
  adversária e o controle do meio. O estilo ajusta: ataque total cria mais e se expõe;
  contra-ataque cria menos, converte mais e cede menos.
- **Finalização.** Quem chuta é sorteado entre os jogadores de ataque, com os melhores
  aparecendo mais. A conversão compara o finalizador com o goleiro: atacante bom contra
  goleiro ruim vira gol à toa; goleiro excelente segura até atacante bom.
- **Bola parada.** Parte das chances é falta ou pênalti, batidos pelos cobradores
  escolhidos em `mm!tatica` (ou, sem cobrador, pelo melhor batedor do time).
- **Andamento.** O ritmo cresce no segundo tempo; quem perde se lança e quem ganha
  administra; expulsão deixa o time com um a menos; depois de um gol ou de um lance
  perigoso pode vir um momento de pressão; o mandante tem o apoio da torcida.
- **Lances variados.** Gols de cabeça, de fora da área, de escanteio, no rebote, no
  contra-ataque e contra; gol anulado por impedimento; milagre do goleiro. No apito
  final saem posse, chutes, notas de cada jogador e o melhor em campo.
"""

from __future__ import annotations

import math
import random
from dataclasses import asdict, dataclass, field
from typing import Any, Sequence

from src.positions import fit
from src.teams import POSITION_GROUPS, TeamError

HALF_LENGTH = 45
YELLOW_CARD_CHANCE = 0.018  # por minuto e por time

# Peso de cada vaga no ataque, na defesa e no controle do meio-campo.
ATTACK_WEIGHT = {
    "CA": 1.0,
    "PE": 0.8,
    "PD": 0.8,
    "MEI": 0.6,
    "MC": 0.3,
    "LE": 0.25,
    "LD": 0.25,
    "ZAG": 0.08,
    "GOL": 0.0,
}
DEFENSE_WEIGHT = {
    "CA": 0.08,
    "PE": 0.2,
    "PD": 0.2,
    "MEI": 0.3,
    "MC": 0.65,
    "LE": 0.75,
    "LD": 0.75,
    "ZAG": 1.0,
    "GOL": 0.0,
}
CONTROL_WEIGHT = {
    "MC": 1.0,
    "MEI": 0.9,
    "PE": 0.4,
    "PD": 0.4,
    "LE": 0.35,
    "LD": 0.35,
    "CA": 0.2,
    "ZAG": 0.2,
    "GOL": 0.0,
}
# Volumes de um 4-3-3 (referência para o número de chances).
REFERENCE_ATTACK_VOLUME = 4.46
REFERENCE_DEFENSE_VOLUME = 5.58

BASE_CHANCES = 4.25  # chances perigosas por time em jogo equilibrado
QUALITY_FACTOR = 0.018  # por ponto de ataque acima da defesa adversária
VOLUME_EXPONENT = 1.0
CONTROL_FACTOR = 0.01
CAPTAIN_CONTROL = 1.5
MIN_CHANCES, MAX_CHANCES = 0.8, 12.0

BASE_CONVERSION = 0.32
FINISHING_FACTOR = 0.02  # por ponto do finalizador acima do goleiro
MIN_CONVERSION, MAX_CONVERSION = 0.06, 0.70
SHOOTER_FACTOR = 0.06  # os melhores atacantes finalizam mais

PENALTY_SHARE = 0.04
FREE_KICK_SHARE = 0.07
# (chances criadas, chances cedidas ao adversário, conversão)
STYLES = {
    "balanced": (1.00, 1.00, 1.00),
    "attack": (1.18, 1.12, 0.97),
    "counter": (0.86, 0.90, 1.12),
}
STYLE_FACTORS = STYLES  # nome antigo


@dataclass(frozen=True)
class TeamStrength:
    attack: float
    resistance: float
    groups: dict[str, float]


@dataclass(frozen=True)
class TeamProfile:
    """Como o time joga, calculado jogador a jogador."""

    attack: float
    attack_volume: float
    defense: float
    defense_volume: float
    control: float
    keeper: float
    ratings: dict[int, float]
    strength: TeamStrength


@dataclass(frozen=True)
class Goal:
    minute: int
    side: str
    scorer_id: int
    scorer_name: str
    stoppage: int = 0
    play: str | None = None


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
    # Tipo do lance: "penalti", "falta", "cabeca", "fora", "escanteio", "rebote",
    # "contra_ataque" ou "contra" (gol contra; `player` é quem desviou).
    play: str | None = None
    # Só no apito final: posse, chutes, notas e melhor em campo (`_match_stats`).
    stats: dict[str, Any] | None = None

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
    stats: dict[str, Any] = field(default_factory=dict)

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


def slot_value(player: dict[str, Any]) -> float:
    """O que o jogador rende na vaga em que foi escalado."""
    slot = player["position"]
    natural = player.get("natural_position") or slot
    factor = fit(natural, slot) or 0.7  # escalação antiga fora da tabela: rende pouco
    return _effective_overall(player) * factor


def _weighted(players, ratings, weights) -> tuple[float, float]:
    volume = sum(weights[player["position"]] for player in players)
    if not volume:
        return 0.0, 0.0
    total = sum(weights[player["position"]] * ratings[id(player)] for player in players)
    return total / volume, volume


def team_profile(
    players: Sequence[dict[str, Any]], tactics: dict[str, Any] | None = None
) -> TeamProfile:
    if len(players) != 11:
        raise TeamError("A simulação exige exatamente 11 titulares.")
    groups: dict[str, list[float]] = {"G": [], "D": [], "M": [], "A": []}
    ratings: dict[int, float] = {}
    for player in players:
        group = POSITION_GROUPS.get(player.get("position"))
        if group is None:
            raise TeamError(f"Posição inválida na escalação: {player.get('position')}.")
        ratings[id(player)] = slot_value(player)
        groups[group].append(ratings[id(player)])
    if any(not values for values in groups.values()):
        raise TeamError("A escalação precisa preencher goleiro, defesa, meio e ataque.")
    attack, attack_volume = _weighted(players, ratings, ATTACK_WEIGHT)
    defense, defense_volume = _weighted(players, ratings, DEFENSE_WEIGHT)
    control, _ = _weighted(players, ratings, CONTROL_WEIGHT)
    captain_id = (tactics or {}).get("captain_player_id")
    if captain_id is not None and any(player.get("id") == captain_id for player in players):
        control += CAPTAIN_CONTROL
    keeper = max(groups["G"])
    averages = {group: sum(values) / len(values) for group, values in groups.items()}
    return TeamProfile(
        attack=attack,
        attack_volume=attack_volume,
        defense=defense,
        defense_volume=defense_volume,
        control=control,
        keeper=keeper,
        ratings=ratings,
        strength=TeamStrength(attack, defense, averages),
    )


def calculate_team_strength(players: Sequence[dict[str, Any]]) -> TeamStrength:
    return team_profile(players).strength


def expected_chances(own: TeamProfile, opponent: TeamProfile) -> float:
    """Chances perigosas que `own` cria contra `opponent` (sem estilo)."""
    quality = math.exp(QUALITY_FACTOR * (own.attack - opponent.defense))
    volume = (
        (own.attack_volume / REFERENCE_ATTACK_VOLUME)
        / (opponent.defense_volume / REFERENCE_DEFENSE_VOLUME)
    ) ** VOLUME_EXPONENT
    control = math.exp(CONTROL_FACTOR * (own.control - opponent.control))
    return BASE_CHANCES * quality * volume * control


def conversion(shooter_rating: float, keeper_rating: float, style_factor: float = 1.0) -> float:
    value = BASE_CONVERSION * math.exp(FINISHING_FACTOR * (shooter_rating - keeper_rating))
    return min(MAX_CONVERSION, max(MIN_CONVERSION, value * style_factor))


def expected_goals(attack: float, resistance: float) -> float:
    """Gols esperados de um ataque contra uma defesa, sem olhar jogador a jogador."""
    chances = BASE_CHANCES * math.exp(QUALITY_FACTOR * (attack - resistance))
    return round(min(3.5, max(0.2, chances * conversion(attack, resistance))), 2)


def _shooter_weights(players, profile: TeamProfile) -> list[float]:
    return [
        ATTACK_WEIGHT[player["position"]]
        * math.exp(SHOOTER_FACTOR * (profile.ratings[id(player)] - 75))
        for player in players
    ]


def _set_piece_taker(players, profile: TeamProfile, chosen_id) -> dict[str, Any]:
    """Cobrador escolhido na tática; sem escolha, o melhor de frente do time."""
    if chosen_id is not None:
        for player in players:
            if player.get("id") == chosen_id:
                return player
    outfield = [player for player in players if player["position"] != "GOL"]
    return max(
        outfield,
        key=lambda player: profile.ratings[id(player)] * (0.6 + ATTACK_WEIGHT[player["position"]]),
    )


def _assistant(
    players: Sequence[dict[str, Any]],
    scorer: dict[str, Any],
    profile: TeamProfile,
    rng: random.Random,
) -> dict[str, Any] | None:
    options = [player for player in players if player is not scorer and player["position"] != "GOL"]
    if not options or rng.random() < 0.25:
        return None
    weights = [
        CONTROL_WEIGHT[player["position"]]
        + ATTACK_WEIGHT[player["position"]] * 0.8
        + 0.1 * math.exp(0.04 * (profile.ratings[id(player)] - 75))
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
)
PLAY_GOAL_LINES = {
    "penalti": "⚽ **GOL DE PÊNALTI!** {scorer} desloca o goleiro e marca para o {team}!",
    "falta": "⚽ **GOL DE FALTA!** {scorer} coloca a bola no ângulo para o {team}!",
    "cabeca": "⚽ **GOL DE CABEÇA!** {scorer} sobe mais que todo mundo e marca para o {team}!",
    "fora": "⚽ **QUE GOLAÇO!** {scorer} solta a bomba de fora da área para o {team}!",
    "contra_ataque": "⚽ **GOL DE CONTRA-ATAQUE!** {scorer} puxa a corrida e finaliza para o {team}!",
    "rebote": "⚽ **GOL!** O goleiro solta e {scorer} pega o rebote para o {team}!",
    "escanteio": "⚽ **GOL DE ESCANTEIO!** {scorer} desvia na pequena área para o {team}!",
    "contra": "⚽ **GOL CONTRA!** {scorer} tenta cortar e manda para as próprias redes. Gol do {team}!",
}
LATE_GOAL = " ⏱️ **No apagar das luzes!**"
SAVE_LINES = (
    "🧤 {shooter} arrisca e {keeper} faz grande defesa!",
    "🧤 Que defesa de {keeper}! Impediu o gol de {shooter}.",
    "🧤 {shooter} cara a cara… {keeper} fecha o ângulo e salva!",
)
MIRACLE_LINES = (
    "🧤✨ **MILAGRE!** {keeper} voa e tira a bola de {shooter} quando todo mundo gritava gol!",
    "🧤✨ **INACREDITÁVEL!** {shooter} chuta no ângulo e {keeper} busca com a ponta dos dedos!",
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
PRESSURE_LINES = (
    "🔥 O {team} se lança ao ataque e encurrala o adversário!",
    "🔥 Pressão total do {team}! A torcida empurra o time.",
    "🔥 O {team} sente o momento e parte para cima!",
)

# Tipo de cada chance de jogada: (tipo, participação, multiplicador da conversão).
OPEN_PLAYS = (
    (None, 0.55, 1.0),
    ("cabeca", 0.15, 0.9),
    ("fora", 0.12, 0.55),
    ("escanteio", 0.07, 0.8),
    ("rebote", 0.05, 1.3),
    ("contra_ataque", 0.06, 1.25),
)
# Quem cabeceia num escanteio: zagueiros e centroavantes.
HEADER_WEIGHT = {
    "ZAG": 1.0,
    "CA": 1.0,
    "MC": 0.35,
    "LE": 0.2,
    "LD": 0.2,
    "MEI": 0.15,
    "PE": 0.15,
    "PD": 0.15,
    "GOL": 0.0,
}
# Quem chuta de fora: meias e volantes aparecem mais.
LONG_SHOT_WEIGHT = {
    "MEI": 1.0,
    "MC": 0.8,
    "PE": 0.6,
    "PD": 0.6,
    "CA": 0.5,
    "LE": 0.25,
    "LD": 0.25,
    "ZAG": 0.1,
    "GOL": 0.0,
}
OWN_GOAL_CHANCE = 0.025  # das chances de jogada que viram gol
OFFSIDE_CHANCE = 0.05  # gols anulados por impedimento
MIRACLE_THRESHOLD = 0.3  # defesa de chance clara (conversão acima disso) pode ser milagre
MIRACLE_SHARE = 0.25
SECOND_YELLOW_CHANCE = 0.15  # quem já tem amarelo se cuida; nem toda falta vira o segundo

# Dinâmica do jogo.
HOME_ADVANTAGE = (1.07, 0.95)  # chances do mandante e do visitante
TRAILING_BOOST = (1.0, 1.12, 1.22)  # por gols atrás (0, 1, 2+)
LEADING_DAMPING = 0.92  # quem está na frente administra
EXPOSED_BOOST = 1.06  # e cede espaço quando o adversário se lança
RED_CARD_ATTACK = 0.7  # chances de quem joga com um a menos
RED_CARD_EXPOSURE = 1.15  # chances do adversário
DIRECT_RED_CHANCE = 0.0002  # por minuto e por time (~1 expulsão a cada 10 jogos no total)
PRESSURE_BOOST = 1.6
PRESSURE_MINUTES = 5
PRESSURE_COOLDOWN = 15


def _tempo(minute: int, stoppage: int) -> float:
    """Ritmo do jogo: começa estudado e esquenta com o cansaço do segundo tempo."""
    if stoppage:
        return 1.3 if minute == 90 else 1.0
    if minute <= HALF_LENGTH:
        return 0.85 + 0.15 * minute / HALF_LENGTH
    return 0.95 + 0.3 * (minute - HALF_LENGTH) / HALF_LENGTH


def _pick(players, weights: dict[str, float], profile: TeamProfile, rng) -> dict[str, Any]:
    values = [
        weights[player["position"]] * math.exp(0.04 * (profile.ratings[id(player)] - 75))
        for player in players
    ]
    return rng.choices(players, weights=values, k=1)[0]


def _possession(profiles, home_style: str, away_style: str, rng) -> int:
    lean = {"balanced": 0, "attack": 4, "counter": -5}
    value = (
        50
        + 1.5 * (profiles["home"].control - profiles["away"].control)
        + lean[home_style]
        - lean[away_style]
        + rng.uniform(-3, 3)
    )
    return round(min(72, max(28, value)))


def _player_ratings(players, profiles, events, score, rng) -> dict[str, dict[str, float]]:
    """Nota de 0 a 10 de cada titular, a partir do que ele fez na partida."""
    deltas: dict[tuple[str, str], float] = {}

    def bump(side: str, name: str, value: float) -> None:
        deltas[(side, name)] = deltas.get((side, name), 0) + value

    rival = {"home": "away", "away": "home"}
    for event in events:
        if event.side is None or event.player is None:
            continue
        if event.kind == "goal":
            if event.play == "contra":
                bump(rival[event.side], event.player, -1.0)
            else:
                bump(event.side, event.player, 1.2)
                if event.detail:
                    bump(event.side, event.detail, 0.7)
        elif event.kind in {"save", "miracle"} and event.detail:
            bump(rival[event.side], event.detail, 0.6 if event.kind == "miracle" else 0.3)
        elif event.kind == "card":
            bump(event.side, event.player, -0.3)
        elif event.kind == "red":
            bump(event.side, event.player, -1.5)
        elif event.kind == "miss":
            bump(event.side, event.player, -0.1)
    ratings: dict[str, dict[str, float]] = {}
    for side, other in (("home", "away"), ("away", "home")):
        conceded = score[other]
        result = 0.3 if score[side] > conceded else (-0.2 if score[side] < conceded else 0)
        ratings[side] = {}
        for player in players[side]:
            position = player["position"]
            value = 6.0 + 0.03 * (profiles[side].ratings[id(player)] - 75) + result
            if position in {"GOL", "ZAG", "LE", "LD"}:
                value += 0.5 if conceded == 0 else -0.25 * conceded
            value += deltas.get((side, _name(player)), 0) + rng.uniform(-0.3, 0.3)
            ratings[side][_name(player)] = round(min(10.0, max(3.0, value)), 1)
    return ratings


def _match_stats(events, possession: int, ratings) -> dict[str, Any]:
    shots = {"home": 0, "away": 0}
    on_target = {"home": 0, "away": 0}
    for event in events:
        if event.side is None:
            continue
        if event.kind in {"goal", "save", "miracle", "post", "miss", "offside"}:
            if event.play != "contra":
                shots[event.side] += 1
        if event.kind in {"goal", "save", "miracle"} and event.play != "contra":
            on_target[event.side] += 1
    best_side, best_name = max(
        ((side, name) for side in ratings for name in ratings[side]),
        key=lambda item: ratings[item[0]][item[1]],
    )
    return {
        "possession": [possession, 100 - possession],
        "shots": [shots["home"], shots["away"]],
        "on_target": [on_target["home"], on_target["away"]],
        "ratings": ratings,
        "best": {"side": best_side, "name": best_name, "rating": ratings[best_side][best_name]},
    }


def _expected_goals(players, profile, opponent, chances, style_conversion, tactics) -> float:
    weights = _shooter_weights(players, profile)
    total = sum(weights) or 1.0
    open_play = (
        sum(
            weight * conversion(profile.ratings[id(player)], opponent.keeper, style_conversion)
            for weight, player in zip(weights, players)
        )
        / total
    )
    free_kick = _set_piece_taker(players, profile, (tactics or {}).get("free_kick_player_id"))
    penalty = _set_piece_taker(players, profile, (tactics or {}).get("penalty_player_id"))
    share_open = 1 - PENALTY_SHARE - FREE_KICK_SHARE
    per_chance = (
        share_open * open_play * sum(share * factor for _, share, factor in OPEN_PLAYS)
        + FREE_KICK_SHARE * _free_kick_conversion(profile.ratings[id(free_kick)], opponent.keeper)
        + PENALTY_SHARE * _penalty_conversion(profile.ratings[id(penalty)], opponent.keeper)
    )
    return round(chances * per_chance, 2)


def _free_kick_conversion(shooter: float, keeper: float) -> float:
    return min(0.3, max(0.04, 0.12 * math.exp(0.05 * (shooter - keeper))))


def _penalty_conversion(shooter: float, keeper: float) -> float:
    return min(0.92, max(0.55, 0.76 + 0.006 * (shooter - keeper)))


def simulate_match(
    home_name: str,
    home_players: Sequence[dict[str, Any]],
    away_name: str,
    away_players: Sequence[dict[str, Any]],
    *,
    home_style: str = "balanced",
    away_style: str = "balanced",
    home_tactics: dict[str, Any] | None = None,
    away_tactics: dict[str, Any] | None = None,
    home_advantage: bool = False,
    rng: random.Random | None = None,
) -> MatchResult:
    """Simula 90 minutos. `*_tactics` traz capitão e cobradores (`team_tactics`).

    O jogo muda com o andamento: esquenta no segundo tempo, quem está perdendo se
    lança, quem está ganhando administra, expulsão deixa o time com um a menos e há
    momentos de pressão depois de gols e lances perigosos. `home_advantage` dá o
    empurrão da torcida ao mandante.
    """
    generator = rng or random.SystemRandom()
    if home_style not in STYLES or away_style not in STYLES:
        raise TeamError("Estilo de time inválido.")
    profiles = {
        "home": team_profile(home_players, home_tactics),
        "away": team_profile(away_players, away_tactics),
    }
    lineups = {"home": list(home_players), "away": list(away_players)}
    on_pitch = {"home": list(home_players), "away": list(away_players)}
    names = {"home": home_name, "away": away_name}
    tactics = {"home": home_tactics or {}, "away": away_tactics or {}}
    styles = {"home": STYLES[home_style], "away": STYLES[away_style]}
    chances: dict[str, float] = {}
    for side, other in (("home", "away"), ("away", "home")):
        created = expected_chances(profiles[side], profiles[other])
        created *= styles[side][0] * styles[other][1]
        chances[side] = min(MAX_CHANCES, max(MIN_CHANCES, created))
    expected = {
        side: _expected_goals(
            lineups[side],
            profiles[side],
            profiles[other],
            chances[side],
            styles[side][2],
            tactics[side],
        )
        for side, other in (("home", "away"), ("away", "home"))
    }
    venue = dict(zip(("home", "away"), HOME_ADVANTAGE if home_advantage else (1.0, 1.0)))

    score = {"home": 0, "away": 0}
    goals: list[Goal] = []
    events: list[MatchEvent] = []
    booked: set[tuple[str, int]] = set()
    sent_off = {"home": 0, "away": 0}
    pressure_until = {"home": -1, "away": -1}
    last_pressure = {"home": -PRESSURE_COOLDOWN, "away": -PRESSURE_COOLDOWN}

    def add(minute, stoppage, kind, text, side=None, player=None, detail=None, play=None):
        events.append(
            MatchEvent(
                minute,
                kind,
                text,
                side,
                score["home"],
                score["away"],
                stoppage,
                player,
                detail,
                play,
            )
        )

    def start_pressure(side: str, step: int, minute: int, stoppage: int) -> None:
        if step - last_pressure[side] < PRESSURE_COOLDOWN or step >= len(timeline) - 3:
            return
        pressure_until[side] = step + PRESSURE_MINUTES
        last_pressure[side] = step
        add(
            minute,
            stoppage,
            "pressure",
            generator.choice(PRESSURE_LINES).format(team=names[side]),
            side,
        )

    def send_off(side: str, player: dict[str, Any], minute: int, stoppage: int, text: str):
        on_pitch[side] = [other for other in on_pitch[side] if other is not player]
        sent_off[side] += 1
        add(minute, stoppage, "red", text, side, _name(player))

    add(1, 0, "kickoff", f"🟢 Rola a bola! {home_name} × {away_name}.")
    first_stoppage = generator.randint(1, 3)
    second_stoppage = generator.randint(2, 5)
    timeline = [(minute, 0) for minute in range(1, HALF_LENGTH + 1)]
    timeline += [(HALF_LENGTH, extra) for extra in range(1, first_stoppage + 1)]
    timeline += [("half", 0)]
    timeline += [(minute, 0) for minute in range(HALF_LENGTH + 1, 91)]
    timeline += [(90, extra) for extra in range(1, second_stoppage + 1)]
    total_tempo = sum(_tempo(minute, extra) for minute, extra in timeline if minute != "half")

    for step, (minute, stoppage) in enumerate(timeline):
        if minute == "half":
            add(
                HALF_LENGTH,
                first_stoppage,
                "halftime",
                f"⏸️ **Intervalo:** {home_name} {score['home']} × {score['away']} {away_name}.",
            )
            add(HALF_LENGTH + 1, 0, "second_half", "🔁 Bola rolando para o segundo tempo!")
            continue
        for side, other in (("home", "away"), ("away", "home")):
            team_name = names[side]
            squad = on_pitch[side]
            profile = profiles[side]
            keeper_player = _goalkeeper(lineups[other])
            keeper_rating = profiles[other].keeper
            behind = score[other] - score[side]
            rate = chances[side] * _tempo(minute, stoppage) / total_tempo * venue[side]
            if behind > 0:
                rate *= TRAILING_BOOST[min(behind, 2)]
            elif behind < 0:
                rate *= LEADING_DAMPING * EXPOSED_BOOST
            rate *= RED_CARD_ATTACK ** sent_off[side] * RED_CARD_EXPOSURE ** sent_off[other]
            if step <= pressure_until[side]:
                rate *= PRESSURE_BOOST
            if generator.random() < rate:
                roll = generator.random()
                if roll < PENALTY_SHARE:
                    play = "penalti"
                    shooter = _set_piece_taker(
                        squad, profile, tactics[side].get("penalty_player_id")
                    )
                    probability = _penalty_conversion(profile.ratings[id(shooter)], keeper_rating)
                elif roll < PENALTY_SHARE + FREE_KICK_SHARE:
                    play = "falta"
                    shooter = _set_piece_taker(
                        squad, profile, tactics[side].get("free_kick_player_id")
                    )
                    probability = _free_kick_conversion(profile.ratings[id(shooter)], keeper_rating)
                else:
                    shares = [share for _, share, _ in OPEN_PLAYS]
                    if behind < 0 or styles[side] == STYLES["counter"]:
                        shares[-1] *= 2.5  # mais espaço para o contra-ataque
                    play, _, factor = generator.choices(OPEN_PLAYS, weights=shares, k=1)[0]
                    if play in {"cabeca", "escanteio"}:
                        shooter = _pick(squad, HEADER_WEIGHT, profile, generator)
                    elif play == "fora":
                        shooter = _pick(squad, LONG_SHOT_WEIGHT, profile, generator)
                    else:
                        shooter = generator.choices(
                            squad, weights=_shooter_weights(squad, profile), k=1
                        )[0]
                    probability = min(
                        MAX_CONVERSION,
                        conversion(profile.ratings[id(shooter)], keeper_rating, styles[side][2])
                        * factor,
                    )
                if generator.random() < probability:
                    if play not in {"penalti", "falta"} and generator.random() < OFFSIDE_CHANCE:
                        add(
                            minute,
                            stoppage,
                            "offside",
                            f"🚩 **Gol anulado!** {_name(shooter)} estava impedido.",
                            side,
                            _name(shooter),
                        )
                        continue
                    if play not in {"penalti", "falta"} and generator.random() < OWN_GOAL_CHANCE:
                        play = "contra"
                        shooter = _pick(on_pitch[other], DEFENSE_WEIGHT, profiles[other], generator)
                    score[side] += 1
                    goals.append(
                        Goal(minute, side, int(shooter["id"]), _name(shooter), stoppage, play)
                    )
                    assistant = (
                        _assistant(squad, shooter, profile, generator)
                        if play not in {"penalti", "falta", "contra", "rebote"}
                        else None
                    )
                    template = PLAY_GOAL_LINES.get(play) or generator.choice(GOAL_LINES)
                    text = template.format(team=team_name, scorer=_name(shooter))
                    if assistant is not None:
                        text += f" Assistência de {_name(assistant)}."
                    if minute >= 88:
                        text += LATE_GOAL
                    add(
                        minute,
                        stoppage,
                        "goal",
                        text,
                        side,
                        _name(shooter),
                        _name(assistant) if assistant is not None else None,
                        play,
                    )
                    # Quem sofre o gol tenta reagir na hora.
                    if score[other] <= score[side] and generator.random() < 0.4:
                        start_pressure(other, step, minute, stoppage)
                else:
                    # Goleiro bom defende mais; o resto vai na trave ou para fora.
                    save = min(0.75, max(0.3, 0.5 + 0.012 * (keeper_rating - 75)))
                    outcome = generator.random()
                    if outcome < save:
                        kind = (
                            "miracle"
                            if probability >= MIRACLE_THRESHOLD
                            and generator.random() < MIRACLE_SHARE
                            else "save"
                        )
                        lines = MIRACLE_LINES if kind == "miracle" else SAVE_LINES
                    elif outcome < save + 0.12:
                        kind, lines = "post", POST_LINES
                    else:
                        kind, lines = "miss", MISS_LINES
                    keeper = _name(keeper_player)
                    add(
                        minute,
                        stoppage,
                        kind,
                        generator.choice(lines).format(
                            shooter=_name(shooter), keeper=keeper, team=team_name
                        ),
                        side,
                        _name(shooter),
                        keeper if kind in {"save", "miracle"} else None,
                        play,
                    )
                    if kind in {"post", "miracle"} and generator.random() < 0.35:
                        start_pressure(side, step, minute, stoppage)
            outfield = [player for player in squad if player["position"] != "GOL"]
            if generator.random() < DIRECT_RED_CHANCE * (1 + step / len(timeline)):
                fouler = generator.choice(outfield)
                send_off(
                    side,
                    fouler,
                    minute,
                    stoppage,
                    f"🟥 **Expulso!** {_name(fouler)} entra duro e recebe o vermelho direto. "
                    f"{team_name} com um a menos.",
                )
            elif generator.random() < YELLOW_CARD_CHANCE:
                fouler = generator.choice(outfield)
                key = (side, int(fouler["id"]))
                if key in booked:
                    if generator.random() >= SECOND_YELLOW_CHANCE:
                        continue
                    send_off(
                        side,
                        fouler,
                        minute,
                        stoppage,
                        f"🟨🟥 **Segundo amarelo!** {_name(fouler)} está fora. "
                        f"{team_name} com um a menos.",
                    )
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
    ratings = _player_ratings(lineups, profiles, events, score, generator)
    stats = _match_stats(events, _possession(profiles, home_style, away_style, generator), ratings)
    events.append(
        MatchEvent(
            90,
            "fulltime",
            f"🏁 **Fim de jogo:** {home_name} {score['home']} × {score['away']} {away_name}.",
            None,
            score["home"],
            score["away"],
            second_stoppage,
            stats=stats,
        )
    )
    return MatchResult(
        home_name=home_name,
        away_name=away_name,
        home_goals=score["home"],
        away_goals=score["away"],
        home_xg=expected["home"],
        away_xg=expected["away"],
        home_strength=profiles["home"].strength,
        away_strength=profiles["away"].strength,
        goals=tuple(goals),
        events=tuple(events),
        stats=stats,
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
