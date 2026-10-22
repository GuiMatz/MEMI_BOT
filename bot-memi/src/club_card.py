"""Cartão do clube no `mm!time`: emblema, cosméticos equipados e números do time."""

from __future__ import annotations

from io import BytesIO
from typing import Any

from PIL import Image, ImageDraw

from src.art import artwork
from src.visual import (
    DRAW,
    LOSS,
    MUTED,
    WIN,
    emblem,
    fit,
    font,
    glow,
    mix,
    rgb,
    text_center,
    to_png,
)

WIDTH, HEIGHT = 1100, 620
BACKGROUND = (14, 18, 25)
SPLIT = 380
FORM_COLORS = {"V": WIN, "E": DRAW, "D": LOSS}
# (rótulo, chave em `info`, medalhão)
ROWS = (
    ("LIGA", "liga", "liga"),
    ("ESTÁDIO", "estadio", "estadio"),
    ("TÉCNICO", "tecnico", "tecnico"),
    ("MASCOTE", "mascote", "mascote"),
    ("UNIFORME", "uniforme", "uniforme"),
    ("ELENCO", "elenco", "emblema"),
)


def _tracked(draw, xy, text: str, size: int, fill, spacing: int = 4, anchor: str = "lm") -> None:
    face = font(size, True)
    x, y = xy
    if anchor == "mm":
        total = sum(draw.textlength(c, font=face) for c in text) + spacing * (len(text) - 1)
        x -= total / 2
    for letter in text:
        draw.text((x, y), letter, font=face, fill=fill, anchor="lm")
        x += draw.textlength(letter, font=face) + spacing


def render_club_card(team: dict[str, Any], info: dict[str, Any]) -> BytesIO:
    color = rgb(team.get("color") or "#2E8B57")
    image = Image.new("RGBA", (WIDTH, HEIGHT), (*BACKGROUND, 255))
    side = Image.new("RGBA", (SPLIT, HEIGHT), (*mix(BACKGROUND, color, 0.12), 255))
    image.alpha_composite(side)
    image.alpha_composite(glow((WIDTH, HEIGHT), (SPLIT / 2, 230), 200, color, 90))
    draw = ImageDraw.Draw(image)
    draw.rectangle((SPLIT - 3, 0, SPLIT, HEIGHT), fill=color)

    badge = emblem(team, 230)
    image.alpha_composite(badge, (SPLIT // 2 - badge.width // 2, 230 - badge.height // 2))
    _tracked(draw, (SPLIT / 2, 410), "FORMA RECENTE", 14, MUTED, 4, "mm")
    form = list(info.get("form") or [])[:5]
    if form:
        size, gap = 40, 10
        left = SPLIT / 2 - (len(form) * size + (len(form) - 1) * gap) / 2
        for index, result in enumerate(form):
            x = left + index * (size + gap)
            draw.rounded_rectangle(
                (x, 434, x + size, 434 + size), 8, fill=FORM_COLORS.get(result, DRAW)
            )
            text_center(draw, (x + size / 2, 434 + size / 2), result, 20)
    else:
        text_center(draw, (SPLIT / 2, 454), "sem jogos ainda", 18, False, MUTED)
    titles = int(info.get("trophies") or 0)
    _tracked(
        draw,
        (SPLIT / 2, 540),
        f"{titles} TÍTULO{'S' if titles != 1 else ''}",
        18,
        (255, 255, 255),
        4,
        "mm",
    )

    left = SPLIT + 50
    name = fit(draw, str(team.get("team_name") or "Meu time"), font(50, True), WIDTH - left - 40)
    draw.text((left, 70), name, font=font(50, True), fill="white", anchor="lm")
    subtitle = f"{team.get('formation') or '4-3-3'}  ·  {info.get('style') or 'equilibrado'}"
    draw.text((left, 118), subtitle, font=font(22), fill=MUTED, anchor="lm")

    column_width = (WIDTH - left - 40) / 2
    for index, (label, key, art) in enumerate(ROWS):
        x = left + (index % 2) * column_width
        y = 172 + (index // 2) * 92
        medal = artwork(art)
        if medal is not None:
            image.alpha_composite(medal.resize((56, 56), Image.LANCZOS), (int(x), int(y)))
        _tracked(draw, (x + 70, y + 16), label, 13, MUTED, 3)
        value = info.get(key)
        text = str(value) if value not in (None, "") else "—"
        fill = "white" if value not in (None, "") else (110, 120, 135)
        size = 22
        while size > 16 and draw.textlength(text, font=font(size, True)) > column_width - 90:
            size -= 1
        draw.text(
            (x + 70, y + 42),
            fit(draw, text, font(size, True), column_width - 90),
            font=font(size, True),
            fill=fill,
            anchor="lm",
        )

    draw.line((left, 460, WIDTH - 40, 460), fill=(40, 48, 62), width=2)
    stats = (
        ("JOGOS", str(info.get("matches") or 0)),
        ("OVR MÉDIO", str(info.get("ovr") or "—")),
        ("ARTILHEIRO", info.get("top_scorer") or "—"),
    )
    widths = (150, 170, WIDTH - left - 40 - 320)
    x = left
    for (label, value), width in zip(stats, widths):
        _tracked(draw, (x, 494), label, 13, MUTED, 3)
        size = 38 if label != "ARTILHEIRO" else 26
        draw.text(
            (x, 540),
            fit(draw, value, font(size, True), width - 10),
            font=font(size, True),
            fill="white",
            anchor="lm",
        )
        x += width
    draw.rectangle((0, HEIGHT - 6, WIDTH, HEIGHT), fill=color)
    return to_png(image)
