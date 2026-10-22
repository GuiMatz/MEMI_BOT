"""Narração ao vivo das partidas: edita um embed lance a lance.

O placar fica fixo no topo, com uma bolinha na cor de cada time. Embaixo vêm os lances
mais recentes primeiro, em linhas curtas (minuto, time, jogador). Cartões e chutes para
fora não poluem o ao vivo: entram no resumo do apito final, que mostra gols, cartões e o
melhor em campo.
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

# Segundos entre lances; gols e intervalo esperam o dobro. O Discord aceita
# cerca de 5 edições a cada 5 segundos por canal, então não use menos que 1.
DEFAULT_DELAY = float(os.getenv("MEMI_NARRATION_DELAY", "1.6"))
VISIBLE_EVENTS = 8
SLOW_EVENTS = {"goal", "halftime", "penalties", "fulltime"}
# Lances que não aparecem no ao vivo (só contam no resumo).
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
    """Linha curta de um lance; None para os que ficam fora do ao vivo."""
    kind = event["kind"]
    if kind in QUIET_EVENTS:
        return None
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
    elif kind == "goal" and player:
        assist = f" · assistência de {event['detail']}" if event.get("detail") else ""
        text = f"{dot} ⚽ **GOL de {player}** · {score}{assist}"
    elif kind == "save" and player:
        keeper = event.get("detail")
        # A bolinha é do time que chutou: a frase começa por quem chutou.
        text = (
            f"{dot} 🧤 {player} chuta, {keeper} defende"
            if keeper
            else (f"{dot} 🧤 {player} chuta e o goleiro defende")
        )
    elif kind == "post" and player:
        text = f"{dot} 🥅 {player} acerta a trave"
    elif kind == "penalty" and player:
        mark = "✅" if event.get("scored", True) else "❌"
        verb = "converte" if event.get("scored", True) else "perde"
        text = f"{dot} {mark} {player} {verb} · {score}"
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
        event["detail"] for event in events if event["kind"] == "save" and event.get("detail")
    )
    if saves:
        keeper, count = saves.most_common(1)[0]
        return f"{keeper} · {count} defesa{'s' if count > 1 else ''}"
    return None


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
            color=color or discord.Color.green(),
        )
        goals = [event for event in regular if event["kind"] == "goal"]
        cards = Counter(event.get("side") for event in regular if event["kind"] == "card")
        for side, name in (("home", home_name), ("away", away_name)):
            scorers = [
                f"`{_clock(event)}` {event.get('player') or 'Gol'}"
                for event in goals
                if event["side"] == side
            ]
            value = "\n".join(scorers) or "—"
            if cards[side]:
                value += f"\n🟨 {cards[side]} {'cartões' if cards[side] > 1 else 'cartão'}"
            embed.add_field(name=f"{dots[side]} {name}", value=value[:1024], inline=True)
        best = _best_player(regular)
        if best:
            embed.add_field(name="⭐ Melhor em campo", value=best, inline=False)
        if penalties:
            embed.set_footer(text=f"Decidido nos pênaltis: {penalties[0]} × {penalties[1]}")
        return embed
    lines = [line for line in (_line(event, dots) for event in events) if line]
    embed = discord.Embed(
        title=f"🏟️ {title}",
        description=f"{board}\n{status}\n\n" + "\n".join(reversed(lines[-VISIBLE_EVENTS:])),
        color=color or discord.Color.red(),
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
        if event["kind"] in QUIET_EVENTS:
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
