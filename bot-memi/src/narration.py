"""Narração ao vivo das partidas: edita um embed lance a lance.

O placar fica fixo no topo, com uma bolinha na cor de cada time. Embaixo os lances correm
de cima para baixo, como um feed: o mais novo entra no fim, em linhas curtas (minuto,
time, jogador). Amarelos e chutes para
fora não poluem o ao vivo: entram no resumo do apito final, que mostra gols, cartões,
posse, chances e o melhor em campo pela nota.
"""

from __future__ import annotations

import asyncio
import colorsys
import os
from collections import Counter
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import asdict, is_dataclass
from typing import Any

import discord

from src.accents import LIVE, NEUTRAL, team_color
from src.commentary import match_commentary, star_lines
from src.simulation import penalty_phrase

# Segundos entre lances; gols e intervalo esperam o dobro. O Discord aceita
# cerca de 5 edições a cada 5 segundos por canal, então não use menos que 1.
DEFAULT_DELAY = float(os.getenv("MEMI_NARRATION_DELAY", "1.6"))
VISIBLE_EVENTS = 8
SLOW_EVENTS = {"goal", "halftime", "penalties", "fulltime", "red", "feat"}
# Como o gol saiu (`MatchEvent.play`).
SET_PIECES = {
    "penalti": " · de pênalti",
    "falta": " · de falta",
    "cabeca": " · de cabeça",
    "fora": " · de fora da área",
    "escanteio": " · de escanteio",
    "rebote": " · no rebote",
    "contra_ataque": " · no contra-ataque",
}
SHOT_VERBS = {
    "penalti": "bate o pênalti",
    "falta": "cobra a falta",
    "cabeca": "cabeceia",
    "fora": "arrisca de fora",
    "escanteio": "cabeceia no escanteio",
    "rebote": "pega o rebote",
    "contra_ataque": "puxa o contra-ataque",
}
SCORER_MARKS = {"penalti": " (pên.)", "contra": " (contra)"}
# Lances menores (amarelo, chute para fora): aparecem na lista, mas não ganham uma edição
# só deles; entram junto com o próximo lance (economiza edições da mensagem).
QUIET_EVENTS = {"card", "miss"}
# Bolinha do Discord por faixa de matiz (graus), para cores saturadas.
HUE_DOTS = ((15, "🔴"), (40, "🟠"), (70, "🟡"), (170, "🟢"), (250, "🔵"), (330, "🟣"), (360, "🔴"))


def team_dot(color: str | None, fallback: str = "⚪") -> str:
    """A bolinha do Discord mais parecida com a cor do time (`#RRGGBB`), pelo matiz."""
    try:
        value = str(color).lstrip("#")
        red, green, blue = (int(value[index : index + 2], 16) / 255 for index in (0, 2, 4))
    except (ValueError, TypeError):
        return fallback
    hue, saturation, brightness = colorsys.rgb_to_hsv(red, green, blue)
    if saturation < 0.25 or brightness < 0.18:
        return "⚪" if brightness > 0.6 else "⚫"
    degrees = hue * 360
    if 15 <= degrees < 45 and brightness < 0.55:
        return "🟤"
    return next(dot for limit, dot in HUE_DOTS if degrees < limit)


def _clock(event: dict[str, Any]) -> str:
    if event["kind"] in {"penalties", "penalty"}:
        return "PEN"
    if event.get("stoppage"):
        return f"{event['minute']}+{event['stoppage']}'"
    return f"{event['minute']}'"


def as_dicts(events: Sequence[Any]) -> list[dict[str, Any]]:
    return [asdict(event) if is_dataclass(event) else dict(event) for event in events]


def _line(event: dict[str, Any], dots: dict[str, str]) -> str | None:
    """Linha curta de um lance."""
    kind = event["kind"]
    dot = dots.get(event.get("side") or "", "▫️")
    player = event.get("player")
    score = f"{event['home_goals']} × {event['away_goals']}"
    if kind == "kickoff":
        text = "🟢 Começa o jogo"
    elif kind == "halftime":
        text = f"⏸️ **Intervalo** · {score}"
    elif kind == "second_half":
        text = "🔁 Começa o segundo tempo"
    elif kind == "fulltime":
        text = f"🏁 **Fim de jogo** · {score}"
    elif kind == "penalties":
        text = "🎯 **Disputa de pênaltis!**"
    elif event.get("shot") and player and kind in {"goal", "save", "miracle", "post", "miss"}:
        # Pênalti no jogo, com o que aconteceu na cobrança.
        scored = kind == "goal"
        phrase = penalty_phrase(scored, event["shot"], event.get("detail"))
        late = " ⏱️" if scored and event["minute"] >= 88 else ""
        head = "⚽ **GOL DE PÊNALTI**" if scored else "❌ **Pênalti perdido**"
        text = f"{dot} {head} · {player} {phrase} · {score}{late}"
    elif kind == "goal" and player:
        assist = f" · assistência de {event['detail']}" if event.get("detail") else ""
        late = " ⏱️" if event["minute"] >= 88 else ""
        if event.get("play") == "contra":
            text = f"{dot} ⚽ **GOL CONTRA** de {player} · {score}{late}"
        else:
            play = SET_PIECES.get(event.get("play"), "")
            text = f"{dot} ⚽ **GOL de {player}**{play} · {score}{assist}{late}"
    elif kind == "miracle" and player:
        text = f"{dot} 🧤✨ **Milagre de {event.get('detail') or 'goleiro'}** no chute de {player}"
    elif kind == "red" and player:
        text = f"{dot} 🟥 **{player} expulso** · time com um a menos"
    elif kind == "offside" and player:
        text = f"{dot} 🚩 Gol anulado · {player} impedido"
    elif kind in {"feat", "pressure", "tactic"}:
        text = f"{dot} {event['text']}"
    elif kind == "save" and player:
        keeper = event.get("detail")
        # A bolinha é do time que chutou: a frase começa por quem chutou.
        verb = SHOT_VERBS.get(event.get("play"), "chuta")
        text = (
            f"{dot} 🧤 {player} {verb}, {keeper} defende"
            if keeper
            else (f"{dot} 🧤 {player} {verb} e o goleiro defende")
        )
    elif kind == "post" and player:
        text = f"{dot} 🥅 {player} acerta a trave"
    elif kind == "miss" and player:
        text = f"{dot} 😩 {player} manda para fora"
    elif kind == "card" and player:
        text = f"{dot} 🟨 {player} leva amarelo"
    elif kind == "penalty" and player:
        scored = event.get("scored", True)
        mark = "✅" if scored else "❌"
        phrase = penalty_phrase(scored, event.get("play"), event.get("detail"))
        text = f"{dot} {mark} {player} {phrase} · {score}"
    else:
        # Lances gravados antes deste formato: usa a frase original.
        text = event["text"]
    return f"`{_clock(event):>6}` {text}"


def _scoreboard(home_name, away_name, events, dots, finished: bool) -> tuple[str, str]:
    regular = [event for event in events if event["kind"] not in {"penalties", "penalty"}]
    last = regular[-1] if regular else {"home_goals": 0, "away_goals": 0}
    shootout = [event for event in events if event["kind"] == "penalty"]
    board = (
        f"### {dots['home']} {home_name}  {last['home_goals']} × {last['away_goals']}  "
        f"{away_name} {dots['away']}"
    )
    if shootout:
        board += f"\n🎯 Pênaltis **{shootout[-1]['home_goals']} × {shootout[-1]['away_goals']}**"
    if finished:
        status = "🏁 **Encerrado**"
    elif events and events[-1]["kind"] == "halftime":
        status = "⏸️ **Intervalo**"
    else:
        status = f"🔴 **AO VIVO** · {_clock(events[-1]) if events else '0'}"
    return board, status


def _best_player(events: Sequence[dict[str, Any]]) -> str | None:
    """Mais gols; no empate, mais assistências; sem gols, o goleiro com mais defesas."""
    goals = Counter(
        event["player"] for event in events if event["kind"] == "goal" and event.get("player")
    )
    assists = Counter(
        event["detail"] for event in events if event["kind"] == "goal" and event.get("detail")
    )
    if goals or assists:
        names = set(goals) | set(assists)
        best = max(names, key=lambda name: (goals[name], assists[name]))
        parts = []
        if goals[best]:
            parts.append(f"{goals[best]} gol{'s' if goals[best] > 1 else ''}")
        if assists[best]:
            parts.append(f"{assists[best]} assistência{'s' if assists[best] > 1 else ''}")
        return f"{best} · {', '.join(parts)}"
    saves = Counter(
        event["detail"]
        for event in events
        if event["kind"] in {"save", "miracle"} and event.get("detail")
    )
    if saves:
        keeper, count = saves.most_common(1)[0]
        return f"{keeper} · {count} defesa{'s' if count > 1 else ''}"
    return None


def _stats_text(stats: dict[str, Any]) -> str:
    """Posse, chances claras e chutes no gol, mandante × visitante."""
    rows = (
        ("Posse", stats["possession"], "%"),
        ("Chances claras", stats["shots"], ""),
        ("No gol", stats["on_target"], ""),
    )
    return "\n".join(
        f"{label}: **{home}{unit}** × **{away}{unit}**" for label, (home, away), unit in rows
    )


def _best_from_stats(stats, events, dots) -> str:
    """Melhor nota da partida, com os gols e as assistências dele."""
    best = stats["best"]
    name, side = best["name"], best["side"]
    goals = sum(
        1
        for event in events
        if event["kind"] == "goal"
        and event.get("player") == name
        and event.get("side") == side
        and event.get("play") != "contra"
    )
    assists = sum(
        1
        for event in events
        if event["kind"] == "goal" and event.get("detail") == name and event.get("side") == side
    )
    parts = [f"nota **{best['rating']:.1f}**"]
    if goals:
        parts.append(f"{goals} gol{'s' if goals > 1 else ''}")
    if assists:
        parts.append(f"{assists} assistência{'s' if assists > 1 else ''}")
    return f"{dots[side]} {name} · {', '.join(parts)}"


# Lances que "pintam" a linha lateral com a cor de quem agiu.
ACTION_EVENTS = {
    "goal",
    "save",
    "miracle",
    "post",
    "red",
    "offside",
    "pressure",
    "penalty",
    "feat",
}


def match_accent(
    events: Sequence[dict[str, Any]],
    home_color: str | None,
    away_color: str | None,
    *,
    finished: bool = False,
) -> discord.Color:
    """A linha lateral acompanha o jogo: quem agiu por último; no fim, o vencedor."""
    colors = {"home": team_color(home_color, LIVE), "away": team_color(away_color, LIVE)}
    if not events:
        return LIVE
    if finished:
        shootout = [event for event in events if event["kind"] == "penalty"]
        last = shootout[-1] if shootout else events[-1]
        home, away = last.get("home_goals", 0), last.get("away_goals", 0)
        if home == away:
            return NEUTRAL
        return colors["home" if home > away else "away"]
    last = events[-1]
    if last["kind"] == "halftime":
        return NEUTRAL
    acted = next(
        (event for event in reversed(events) if event["kind"] in ACTION_EVENTS),
        None,
    )
    if acted is None or acted.get("side") not in colors:
        return LIVE
    return colors[acted["side"]]


def match_embed(
    home_name: str,
    away_name: str,
    events: Sequence[dict[str, Any]],
    *,
    title: str = "Amistoso",
    finished: bool = False,
    penalties: tuple[int, int] | None = None,
    color: discord.Color | None = None,
    home_color: str | None = None,
    away_color: str | None = None,
) -> discord.Embed:
    dots = {"home": team_dot(home_color, "⚪"), "away": team_dot(away_color, "⚫")}
    if dots["home"] == dots["away"]:
        dots["away"] = "⚫" if dots["home"] != "⚫" else "⚪"
    board, status = _scoreboard(home_name, away_name, events, dots, finished)
    regular = [event for event in events if event["kind"] not in {"penalties", "penalty"}]
    if finished:
        embed = discord.Embed(
            title=f"🏟️ {title}",
            description=f"{board}\n{status}",
            color=color or match_accent(events, home_color, away_color, finished=True),
        )
        goals = [event for event in regular if event["kind"] == "goal"]
        cards = Counter(event.get("side") for event in regular if event["kind"] == "card")
        reds = Counter(event.get("side") for event in regular if event["kind"] == "red")
        for side, name in (("home", home_name), ("away", away_name)):
            scorers = [
                f"`{_clock(event)}` {event.get('player') or 'Gol'}"
                f"{SCORER_MARKS.get(event.get('play'), '')}"
                for event in goals
                if event["side"] == side
            ]
            value = "\n".join(scorers) or "—"
            if cards[side]:
                value += f"\n🟨 {cards[side]} {'cartões' if cards[side] > 1 else 'cartão'}"
            if reds[side]:
                value += f"\n🟥 {reds[side]} {'expulsões' if reds[side] > 1 else 'expulsão'}"
            embed.add_field(name=f"{dots[side]} {name}", value=value[:1024], inline=True)
        stats = next((event.get("stats") for event in regular if event["kind"] == "fulltime"), None)
        if stats:
            if stats.get("highlights"):
                embed.add_field(
                    name="🏅 Destaques", value="\n".join(stats["highlights"])[:1024], inline=False
                )
            embed.add_field(name="📊 Estatísticas", value=_stats_text(stats), inline=False)
        best = _best_from_stats(stats, regular, dots) if stats else _best_player(regular)
        if best:
            embed.add_field(name="⭐ Melhor em campo", value=best, inline=False)
        analysis = [match_commentary(events, home_name, away_name), *star_lines(regular)]
        if any(analysis):
            text = "\n".join(f"*{line}*" for line in analysis if line)
            embed.add_field(name="🎙️ Análise", value=text[:1024], inline=False)
        if penalties:
            embed.set_footer(text=f"Decidido nos pênaltis: {penalties[0]} × {penalties[1]}")
        return embed
    lines = [line for line in (_line(event, dots) for event in events) if line]
    embed = discord.Embed(
        title=f"🏟️ {title}",
        description=f"{board}\n{status}\n\n" + "\n".join(lines[-VISIBLE_EVENTS:]),
        color=color or match_accent(events, home_color, away_color),
    )
    return embed


async def narrate(
    send: Callable[[discord.Embed], Awaitable[discord.Message]],
    home_name: str,
    away_name: str,
    events: Sequence[Any],
    *,
    title: str = "Amistoso",
    penalties: tuple[int, int] | None = None,
    delay: float | None = None,
    home_color: str | None = None,
    away_color: str | None = None,
) -> discord.Message:
    """Envia o placar e o atualiza a cada lance até o apito final."""
    pause = DEFAULT_DELAY if delay is None else delay
    colors = {"home_color": home_color, "away_color": away_color}
    timeline = as_dicts(events)
    shown: list[dict[str, Any]] = timeline[:1]
    message = await send(match_embed(home_name, away_name, shown, title=title, **colors))
    for event in timeline[1:]:
        shown.append(event)
        if event["kind"] in QUIET_EVENTS and not event.get("shot"):  # pênalti perdido aparece
            continue  # não aparece no ao vivo: não gasta uma edição
        await asyncio.sleep(pause * (2 if event["kind"] in SLOW_EVENTS else 1))
        try:
            await message.edit(
                embed=match_embed(home_name, away_name, shown, title=title, **colors)
            )
        except discord.HTTPException:
            # Uma edição perdida não pode interromper a partida.
            continue
    await asyncio.sleep(pause)
    final = match_embed(
        home_name, away_name, shown, title=title, finished=True, penalties=penalties, **colors
    )
    try:
        await message.edit(embed=final)
    except discord.HTTPException:
        message = await send(final)
    return message
