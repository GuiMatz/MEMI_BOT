"""Cartão do clube no `mm!time`: emblema, cosméticos equipados e números do time.

O Discord mostra a imagem do embed com ~450 px de largura, então tudo aqui é desenhado
grande (texto de 26 px ou mais numa tela de 1000 px) para continuar legível.
"""

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

WIDTH, HEIGHT = 1000, 600
BACKGROUND = (14, 18, 25)
SPLIT = 300
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


def _fit_size(draw, text: str, size: int, minimum: int, width: float) -> int:
    while size > minimum and draw.textlength(text, font=font(size, True)) > width:
        size -= 1
    return size


def render_club_card(team: dict[str, Any], info: dict[str, Any]) -> BytesIO:
    color = rgb(team.get("color") or "#2E8B57")
    image = Image.new("RGBA", (WIDTH, HEIGHT), (*BACKGROUND, 255))
    image.alpha_composite(Image.new("RGBA", (SPLIT, HEIGHT), (*mix(BACKGROUND, color, 0.14), 255)))
    image.alpha_composite(glow((WIDTH, HEIGHT), (SPLIT / 2, 190), 170, color, 90))
    draw = ImageDraw.Draw(image)
    draw.rectangle((SPLIT - 4, 0, SPLIT, HEIGHT), fill=color)

    # Coluna do emblema: escudo, forma recente e títulos.
    badge = emblem(team, 200)
    image.alpha_composite(badge, (SPLIT // 2 - badge.width // 2, 190 - badge.height // 2))
    text_center(draw, (SPLIT / 2, 340), "FORMA", 20, True, MUTED)
    form = list(info.get("form") or [])[:5]
    if form:
        size, gap = 46, 8
        left = SPLIT / 2 - (len(form) * size + (len(form) - 1) * gap) / 2
        for index, result in enumerate(form):
            x = left + index * (size + gap)
            draw.rounded_rectangle(
                (x, 366, x + size, 366 + size), 10, fill=FORM_COLORS.get(result, DRAW)
            )
            text_center(draw, (x + size / 2, 366 + size / 2), result, 26)
    else:
        text_center(draw, (SPLIT / 2, 389), "sem jogos", 24, False, MUTED)
    titles = int(info.get("trophies") or 0)
    text_center(draw, (SPLIT / 2, 480), str(titles), 56)
    text_center(draw, (SPLIT / 2, 528), "TÍTULO" if titles == 1 else "TÍTULOS", 20, True, MUTED)

    # Coluna da direita: nome, cosméticos e números.
    left = SPLIT + 36
    usable = WIDTH - left - 30
    name = str(team.get("team_name") or "Meu time")
    size = _fit_size(draw, name, 52, 34, usable)
    draw.text(
        (left, 56),
        fit(draw, name, font(size, True), usable),
        font=font(size, True),
        fill="white",
        anchor="lm",
    )
    subtitle = f"{team.get('formation') or '4-3-3'} · {info.get('style') or 'equilibrado'}"
    draw.text((left, 102), subtitle, font=font(26), fill=MUTED, anchor="lm")

    column = usable / 2
    for index, (label, key, art) in enumerate(ROWS):
        x = left + (index % 2) * column
        y = 140 + (index // 2) * 96
        medal = artwork(art)
        if medal is not None:
            image.alpha_composite(medal.resize((60, 60), Image.LANCZOS), (int(x), int(y + 8)))
        draw.text((x + 74, y + 22), label, font=font(19, True), fill=MUTED, anchor="lm")
        value = info.get(key)
        text = str(value) if value not in (None, "") else "—"
        fill = "white" if value not in (None, "") else (110, 120, 135)
        size = _fit_size(draw, text, 28, 20, column - 90)
        draw.text(
            (x + 74, y + 56),
            fit(draw, text, font(size, True), column - 90),
            font=font(size, True),
            fill=fill,
            anchor="lm",
        )

    draw.line((left, 440, WIDTH - 30, 440), fill=(40, 48, 62), width=2)
    stats = (
        ("JOGOS", str(info.get("matches") or 0), 140),
        ("OVR", str(info.get("ovr") or "—"), 130),
        ("ARTILHEIRO", info.get("top_scorer") or "—", usable - 270),
    )
    x = left
    for label, value, width in stats:
        draw.text((x, 478), label, font=font(19, True), fill=MUTED, anchor="lm")
        size = 46 if label != "ARTILHEIRO" else _fit_size(draw, value, 32, 22, width - 10)
        draw.text(
            (x, 530),
            fit(draw, value, font(size, True), width - 10),
            font=font(size, True),
            fill="white",
            anchor="lm",
        )
        x += width
    draw.rectangle((0, HEIGHT - 8, WIDTH, HEIGHT), fill=color)
    return to_png(image)
