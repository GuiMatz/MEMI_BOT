"""A análise do comentarista no fim do jogo: uma frase sobre o que a partida foi.

A partida cai na primeira situação que bate (da mais marcante para a mais comum): virada
insana antes de virada, hat-trick antes de vitória fácil, e assim por diante. As frases
ficam em `src.commentary_phrases`; a escolha evita as usadas há pouco e é sempre a mesma
para a mesma partida.
"""

from __future__ import annotations

import random
from collections import Counter, OrderedDict, deque
from collections.abc import Sequence
from typing import Any

from src.commentary_phrases import PHRASES, RIVALRY, STARS
from src.db import normalize_player_name
from src.team_stats import Rivalry

# Frases usadas há pouco, por situação: a próxima evita repetir (metade das opções fica de
# fora). E a frase de cada partida fica guardada: o embed final pode ser montado mais de
# uma vez e tem de mostrar sempre a mesma. Tudo em memória; recomeça quando o bot reinicia.
_RECENT: dict[str, deque[int]] = {}
_CHOSEN: OrderedDict[str, int] = OrderedDict()
CHOSEN_LIMIT = 500


def pick(group: str, seed: str, phrases: Sequence[str]) -> str:
    """Uma frase de `phrases`, sem repetir as usadas há pouco no mesmo `group`."""
    key = f"{group}|{seed}"
    if key in _CHOSEN:
        _CHOSEN.move_to_end(key)
        return phrases[_CHOSEN[key]]
    recent = _RECENT.setdefault(group, deque(maxlen=max(1, len(phrases) // 2)))
    options = [index for index in range(len(phrases)) if index not in recent]
    index = random.Random(seed).choice(options or range(len(phrases)))
    recent.append(index)
    _CHOSEN[key] = index
    if len(_CHOSEN) > CHOSEN_LIMIT:
        _CHOSEN.popitem(last=False)
    return phrases[index]


def _final_score(regular: Sequence[dict[str, Any]]) -> tuple[int, int]:
    last = regular[-1] if regular else {"home_goals": 0, "away_goals": 0}
    return int(last.get("home_goals", 0)), int(last.get("away_goals", 0))


def _situation(events: Sequence[dict[str, Any]]) -> tuple[str, dict[str, str]] | None:
    """A situação da partida e os nomes para as frases ("winner", "player"…)."""
    regular = [event for event in events if event["kind"] not in {"penalties", "penalty"}]
    if not regular:
        return None
    shootout = [event for event in events if event["kind"] == "penalty"]
    home, away = _final_score(regular)
    goals = [event for event in regular if event["kind"] == "goal"]
    stats = next((event.get("stats") for event in regular if event["kind"] == "fulltime"), None)
    winner = "home" if home > away else "away" if away > home else None
    if winner is None and shootout:
        last = shootout[-1]
        winner = "home" if last["home_goals"] > last["away_goals"] else "away"
        return "penaltis", {"side": winner}
    loser = {"home": "away", "away": "home"}.get(winner or "")
    margin, total = abs(home - away), home + away
    info: dict[str, str] = {"side": winner or ""}

    if winner:
        # Maior desvantagem que o vencedor teve durante o jogo.
        worst = 0
        for event in goals:
            mine, theirs = (
                (event["home_goals"], event["away_goals"])
                if winner == "home"
                else (event["away_goals"], event["home_goals"])
            )
            worst = max(worst, theirs - mine)
        if worst >= 2:
            return "virada_insana", info
        if worst == 1:
            return "virada", info
    scorers = Counter(
        (event["side"], event["player"])
        for event in goals
        if event.get("play") != "contra" and event.get("player")
    )
    if scorers:
        (side, player), count = scorers.most_common(1)[0]
        if count >= 3:
            data = {"side": side, "player": player, "goals": str(count)}
            return ("poker" if count >= 4 else "hattrick"), data
    if winner and margin >= 4:
        return "goleada", info
    if winner is None and any(
        e["kind"] == "feat" and e.get("play") == "empate_fim" for e in regular
    ):
        tied = next(e for e in regular if e["kind"] == "feat" and e.get("play") == "empate_fim")
        return "empate_fim", {"side": tied["side"]}
    if (
        winner
        and margin == 1
        and goals
        and goals[-1]["side"] == winner
        and goals[-1]["minute"] >= 88
    ):
        return "gol_no_fim", info
    shots = (stats or {}).get("shots")
    if winner and shots:
        index = {"home": 0, "away": 1}
        if shots[index[loser]] >= shots[index[winner]] + 4:
            return "injustica", info
    if total == 0:  # o 0 × 0 é a história do jogo, mesmo com goleiro inspirado
        return "zero_a_zero", info
    if total <= 1 and stats and stats.get("best", {}).get("rating", 0) >= 8:
        saves = Counter(
            (event["detail"], event["side"])
            for event in regular
            if event["kind"] in {"save", "miracle"} and event.get("detail")
        )
        if saves:
            (keeper, shooter_side), count = saves.most_common(1)[0]
            if count >= 3:
                side = "away" if shooter_side == "home" else "home"
                return "goleiro", {"side": side, "keeper": keeper}
    if winner and stats:
        index = {"home": 0, "away": 1}
        possession = stats.get("possession") or (50, 50)
        if possession[index[winner]] >= 60 and shots and margin >= 2:
            return "dominio", info
    if winner and margin >= 2:
        return "vitoria_facil", info
    reds = Counter(event.get("side") for event in regular if event["kind"] == "red")
    if winner and reds[loser] and not reds[winner]:
        return "expulsao", info
    if total >= 6:
        return "jogo_louco", info
    if winner is None:
        return "empate", info
    return "jogo_dificil", info


def match_commentary(
    events: Sequence[dict[str, Any]], home_name: str, away_name: str
) -> str | None:
    """A frase do comentarista para a partida encerrada (None se não há lances)."""
    found = _situation(events)
    if found is None:
        return None
    situation, info = found
    names = {"home": home_name, "away": away_name}
    side = info.get("side") or "home"
    other = "away" if side == "home" else "home"
    regular = [event for event in events if event["kind"] not in {"penalties", "penalty"}]
    home, away = _final_score(regular)
    values = {
        "winner": names[side],
        "loser": names[other],
        "team": names[side],
        "other": names[other],
        "home": home_name,
        "away": away_name,
        "player": info.get("player", ""),
        "goals": info.get("goals", ""),
        "keeper": info.get("keeper", ""),
        "score": f"{home} × {away}",
    }
    seed = f"{home_name}|{away_name}|{len(events)}|{home}x{away}|{situation}"
    return pick(situation, seed, PHRASES[situation]).format(**values)


def _star(name: str) -> tuple[str, ...] | None:
    words = normalize_player_name(name).split()
    for keys, exact, phrases in STARS:
        if (words == list(keys)) if exact else set(keys) <= set(words):
            return phrases
    return None


def star_lines(events: Sequence[dict[str, Any]], limit: int = 2) -> list[str]:
    """Frases dos craques que fizeram gol na partida (no máximo `limit`)."""
    lines: list[str] = []
    seen: set[str] = set()
    for event in events:
        name = event.get("player") or ""
        if event["kind"] != "goal" or event.get("play") == "contra" or name in seen:
            continue
        seen.add(name)
        phrases = _star(name)
        if phrases:
            lines.append(pick(f"craque:{name}", f"{name}|{len(events)}", phrases))
        if len(lines) >= limit:
            break
    return lines


MILESTONES = (10, 25, 50, 100)


def _record(rivalry: Rivalry, winner: str, loser: str) -> str:
    """ "Ana FC 5 × 3 Beto FC (2 empates)", do ponto de vista de `winner`."""
    draws = f" ({rivalry.draws} empate{'s' if rivalry.draws > 1 else ''})" if rivalry.draws else ""
    return f"{winner} {rivalry.wins} × {rivalry.losses} {loser}{draws}"


def rivalry_intro(rivalry: Rivalry, home_name: str, away_name: str) -> str:
    """Linha antes do jogo: retrospecto do mandante contra o visitante."""
    if not rivalry.games:
        return f"🆕 Primeiro confronto entre **{home_name}** e **{away_name}**!"
    head = "⚔️ **Clássico!** " if rivalry.games >= 5 else "📜 "
    text = f"{head}Retrospecto: **{home_name}** {rivalry.wins} × {rivalry.losses} **{away_name}**"
    if rivalry.draws:
        text += f" · {rivalry.draws} empate{'s' if rivalry.draws > 1 else ''}"
    for outcome, name in (("V", home_name), ("D", away_name)):
        streak = rivalry.run(outcome)
        if streak >= 2:
            text += f" · 🔥 {name} venceu os últimos {streak}"
    return text


def rivalry_line(rivalry: Rivalry, home_name: str, away_name: str) -> str | None:
    """Depois do jogo (já contado em `rivalry`, do ponto de vista do mandante): uma frase se a
    rivalidade tiver novidade (sequência, fim de jejum, liderança, empate, marco)."""
    if not rivalry.games:
        return None
    last = rivalry.outcomes[-1]
    seed = f"{home_name}|{away_name}|{rivalry.games}|{rivalry.outcomes}"
    values = {"home": home_name, "away": away_name, "n": rivalry.games}
    if last == "E":
        if rivalry.games == 1:
            return pick("rival:primeiro_empate", seed, RIVALRY["primeiro_empate"]).format(**values)
        situation = "marco" if rivalry.games in MILESTONES else None
        if situation is None:
            return None
        values.update(winner=home_name, loser=away_name)
        values["record"] = _record(rivalry, home_name, away_name)
        return pick("rival:marco", seed, RIVALRY["marco"]).format(**values)
    home_won = last == "V"
    view = rivalry if home_won else rivalry.flipped()
    winner, loser = (home_name, away_name) if home_won else (away_name, home_name)
    values.update(winner=winner, loser=loser, record=_record(view, winner, loser))
    streak = view.run("V")
    drought = 0
    for outcome in reversed(view.outcomes[:-1]):
        if outcome == "V":
            break
        drought += 1
    if view.games == 1:
        situation = "primeiro"
    elif streak >= 3:
        situation, values["n"] = "freguesia", streak
    elif drought >= 3:
        situation, values["n"] = "jejum", drought
    elif view.games in MILESTONES:
        situation = "marco"
    elif view.wins > view.losses and view.wins - 1 <= view.losses:
        situation = "virou"
    elif view.wins == view.losses:
        situation = "igualou"
    else:
        return None
    return pick(f"rival:{situation}", seed, RIVALRY[situation]).format(**values)
