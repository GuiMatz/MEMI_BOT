"""Narração ao vivo das partidas: edita um embed lance a lance."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import asdict, is_dataclass
from typing import Any

import discord

# Segundos entre lances; gols e intervalo esperam o dobro. O Discord aceita
# cerca de 5 edições a cada 5 segundos por canal, então não use menos que 1.
DEFAULT_DELAY = float(os.getenv("MEMI_NARRATION_DELAY", "1.6"))
VISIBLE_EVENTS = 12
SLOW_EVENTS = {"goal", "halftime", "penalties", "fulltime"}


def _clock(event: dict[str, Any]) -> str:
    if event["kind"] in {"penalties", "penalty"}:
        return "PEN"
    if event.get("stoppage"):
        return f"{event['minute']}+{event['stoppage']}'"
    return f"{event['minute']}'"


def as_dicts(events: Sequence[Any]) -> list[dict[str, Any]]:
    return [asdict(event) if is_dataclass(event) else dict(event) for event in events]


def match_embed(
    home_name: str,
    away_name: str,
    events: Sequence[dict[str, Any]],
    *,
    title: str = "Amistoso",
    finished: bool = False,
    penalties: tuple[int, int] | None = None,
    color: discord.Color | None = None,
) -> discord.Embed:
    regular = [event for event in events if event["kind"] not in {"penalties", "penalty"}]
    last = regular[-1] if regular else {"home_goals": 0, "away_goals": 0}
    shootout = [event for event in events if event["kind"] == "penalty"]
    score = f"{home_name} **{last['home_goals']} × {last['away_goals']}** {away_name}"
    if shootout:
        score += f"\n🎯 Pênaltis: **{shootout[-1]['home_goals']} × {shootout[-1]['away_goals']}**"
    status = "🏁 Encerrado" if finished else f"🔴 AO VIVO · {_clock(events[-1]) if events else '0'}"
    lines = [f"`{_clock(event):>6}` {event['text']}" for event in events[-VISIBLE_EVENTS:]]
    embed = discord.Embed(
        title=f"🏟️ {title}",
        description=f"{score}\n{status}\n\n" + "\n".join(lines),
        color=color or (discord.Color.green() if finished else discord.Color.red()),
    )
    if finished:
        goals = [event for event in regular if event["kind"] == "goal"]
        for side, name in (("home", home_name), ("away", away_name)):
            scorers = [
                f"`{_clock(event)}` {event.get('player') or 'Gol'}"
                for event in goals
                if event["side"] == side
            ]
            if scorers:
                embed.add_field(name=f"⚽ {name}", value="\n".join(scorers)[:1024], inline=True)
        if penalties:
            embed.set_footer(text=f"Decidido nos pênaltis: {penalties[0]} × {penalties[1]}")
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
) -> discord.Message:
    """Envia o placar e o atualiza a cada lance até o apito final."""
    pause = DEFAULT_DELAY if delay is None else delay
    timeline = as_dicts(events)
    shown: list[dict[str, Any]] = timeline[:1]
    message = await send(match_embed(home_name, away_name, shown, title=title))
    for event in timeline[1:]:
        await asyncio.sleep(pause * (2 if event["kind"] in SLOW_EVENTS else 1))
        shown.append(event)
        try:
            await message.edit(
                embed=match_embed(home_name, away_name, shown, title=title)
            )
        except discord.HTTPException:
            # Uma edição perdida não pode interromper a partida.
            continue
    await asyncio.sleep(pause)
    final = match_embed(
        home_name, away_name, shown, title=title, finished=True, penalties=penalties
    )
    try:
        await message.edit(embed=final)
    except discord.HTTPException:
        message = await send(final)
    return message
