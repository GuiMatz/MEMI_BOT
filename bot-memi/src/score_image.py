"""Cartão do placar final: emblemas, placar, pênaltis e artilheiros dos dois times."""

from __future__ import annotations

from io import BytesIO
from typing import Any, Iterable

from PIL import Image, ImageDraw

from src.visual import (
    MUTED,
    NIGHT,
    apply_grain,
    emblem,
    fit,
    font,
    glow,
    horizontal_gradient,
    mix,
    pill,
    rgb,
    shade,
    shadowed,
    text_center,
    to_png,
    vignette,
)

WIDTH, HEIGHT = 1200, 630
GOLD = (240, 200, 70)
MAX_SCORERS = 4


def _goals_by_side(goals: Iterable[Any], side: str) -> list[str]:
    lines = []
    for goal in goals:
        data = goal if isinstance(goal, dict) else goal.__dict__
        if data.get("side") != side:
            continue
        minute = f"{data['minute']}'" + (f"+{data['stoppage']}" if data.get("stoppage") else "")
        lines.append((minute, str(data.get("scorer_name") or "—")))
    return lines


def _ball(draw: ImageDraw.ImageDraw, x: float, y: float, radius: float) -> None:
    draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill="white")
    draw.regular_polygon((x, y, radius * 0.42), 5, fill=(30, 30, 35))


def render_score_card(
    home: dict[str, Any],
    away: dict[str, Any],
    home_goals: int,
    away_goals: int,
    goals: Iterable[Any] = (),
    *,
    title: str = "Amistoso",
    venue: str | None = None,
    penalties: tuple[int, int] | None = None,
) -> BytesIO:
    home_color = rgb(home.get("color") or "#2E8B57")
    away_color = rgb(away.get("color") or "#3B6FD8")
    final = "final" in title.casefold()
    accent = GOLD if final else (235, 240, 245)

    image = horizontal_gradient((WIDTH, HEIGHT), shade(home_color, 0.55), shade(away_color, 0.55))
    middle = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    ImageDraw.Draw(middle).polygon(
        [(470, 0), (730, 0), (680, HEIGHT), (520, HEIGHT)], fill=(*NIGHT, 235)
    )
    image.alpha_composite(middle.resize((WIDTH, HEIGHT)))
    image.alpha_composite(
        glow((WIDTH, HEIGHT), (230, 250), 190, mix(home_color, (255, 255, 255), 0.2), 120)
    )
    image.alpha_composite(
        glow((WIDTH, HEIGHT), (970, 250), 190, mix(away_color, (255, 255, 255), 0.2), 120)
    )
    if final:
        image.alpha_composite(glow((WIDTH, HEIGHT), (600, 240), 170, GOLD, 70))
    image = apply_grain(image, 10)
    vignette(image, 120)
    draw = ImageDraw.Draw(image)

    pill(draw, (WIDTH // 2, 60), title.upper(), 30, accent)
    if venue:
        text_center(draw, (WIDTH // 2, 114), fit(draw, venue, font(26), 440), 26, False, MUTED)

    for team, center_x in ((home, 230), (away, 970)):
        badge = shadowed(emblem(team, 210), blur=12)
        image.alpha_composite(badge, (center_x - badge.width // 2, 255 - badge.height // 2))
        name = str(team.get("team_name") or "Time")
        size = 44
        while size > 30 and draw.textlength(name, font=font(size, True)) > 430:
            size -= 1
        text_center(draw, (center_x, 400), fit(draw, name, font(size, True), 430), size)

    home_won = home_goals > away_goals or (
        penalties is not None and home_goals == away_goals and penalties[0] > penalties[1]
    )
    away_won = away_goals > home_goals or (
        penalties is not None and home_goals == away_goals and penalties[1] > penalties[0]
    )
    score_font = 150
    text_center(
        draw,
        (WIDTH // 2 - 95, 250),
        str(home_goals),
        score_font,
        fill="white" if home_won or not away_won else (200, 205, 215),
    )
    text_center(draw, (WIDTH // 2, 245), "–", 90, fill=(150, 158, 172))
    text_center(
        draw,
        (WIDTH // 2 + 95, 250),
        str(away_goals),
        score_font,
        fill="white" if away_won or not home_won else (200, 205, 215),
    )
    if penalties:
        text_center(
            draw, (WIDTH // 2, 355), f"pênaltis {penalties[0]} × {penalties[1]}", 32, True, accent
        )
    else:
        text_center(draw, (WIDTH // 2, 355), "FIM DE JOGO", 28, True, MUTED)

    goal_list = list(goals)
    for side, center_x in (("home", 230), ("away", 970)):
        rows = _goals_by_side(goal_list, side)
        shown = rows[:MAX_SCORERS]
        y = 458
        for minute, scorer in shown:
            label = fit(draw, f"{minute}  {scorer}", font(30, True), 400)
            width = draw.textlength(label, font=font(30, True))
            left = center_x - (width + 36) / 2
            _ball(draw, left + 11, y, 11)
            draw.text((left + 32, y), label, font=font(30, True), fill="white", anchor="lm")
            y += 40
        if len(rows) > MAX_SCORERS:
            text_center(draw, (center_x, y), f"+{len(rows) - MAX_SCORERS} gols", 24, False, MUTED)

    draw.rectangle((0, HEIGHT - 10, WIDTH // 2, HEIGHT), fill=home_color)
    draw.rectangle((WIDTH // 2, HEIGHT - 10, WIDTH, HEIGHT), fill=away_color)
    text_center(draw, (WIDTH // 2, HEIGHT - 34), "FUT.AE", 22, True, (190, 198, 210))
    return to_png(image)
