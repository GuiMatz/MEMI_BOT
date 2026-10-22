"""Quadros em imagem: missões (`mm!missoes`) e temporada mensal (`mm!temporada`)."""

from __future__ import annotations

from functools import lru_cache
from io import BytesIO
from typing import Any

from PIL import Image, ImageDraw

from src.art import artwork
from src.db import ROOT
from src.visual import MUTED, emblem, fit, font, glow, mix, text_center, to_png

BACKGROUND = (14, 18, 25)
CARD = (21, 27, 37)
LINE = (40, 48, 62)
CYAN = (63, 198, 216)
GOLD = (232, 184, 74)
SILVER = (192, 200, 212)
BRONZE = (205, 127, 72)


@lru_cache(maxsize=1)
def _coin() -> Image.Image | None:
    path = ROOT.parent / "assets" / "emojis" / "futae" / "memilets.png"
    if not path.is_file():
        return None
    with Image.open(path) as opened:
        return opened.convert("RGBA").resize((30, 30), Image.LANCZOS)


def _tracked(draw, xy, text: str, size: int, fill, spacing: int = 4, anchor: str = "lm") -> None:
    face = font(size, True)
    x, y = xy
    total = sum(draw.textlength(c, font=face) for c in text) + spacing * (len(text) - 1)
    if anchor == "mm":
        x -= total / 2
    elif anchor == "rm":
        x -= total
    for letter in text:
        draw.text((x, y), letter, font=face, fill=fill, anchor="lm")
        x += draw.textlength(letter, font=face) + spacing


def _header(image: Image.Image, title: str, subtitle: str, art: str, accent) -> None:
    draw = ImageDraw.Draw(image)
    medal = artwork(art)
    if medal is not None:
        image.alpha_composite(medal.resize((76, 76), Image.LANCZOS), (40, 30))
    _tracked(draw, (134, 54), title, 26, (255, 255, 255), 6)
    draw.text((134, 90), subtitle, font=font(20), fill=MUTED, anchor="lm")
    draw.line((40, 130, image.width - 40, 130), fill=(*accent, 255), width=2)


def render_missions(rows: list[dict[str, Any]], owner: str) -> BytesIO:
    width = 900
    sections = [("DIÁRIAS", "diaria"), ("SEMANAIS", "semanal")]
    height = (
        160
        + sum(48 + 84 * sum(1 for row in rows if row["scope"] == scope) for _, scope in sections)
        + 20
    )
    image = Image.new("RGBA", (width, height), (*BACKGROUND, 255))
    image.alpha_composite(glow(image.size, (width - 120, 40), 220, CYAN, 40))
    _header(
        image,
        "MISSÕES",
        f"de {owner} · diárias viram à meia-noite, semanais na segunda",
        "missoes",
        CYAN,
    )
    draw = ImageDraw.Draw(image)
    y = 160
    coin = _coin()
    for label, scope in sections:
        _tracked(draw, (40, y + 16), label, 15, MUTED, 5)
        y += 48
        for row in (row for row in rows if row["scope"] == scope):
            done = row["progress"] >= row["goal"]
            box = (40, y, width - 40, y + 70)
            draw.rounded_rectangle(box, 14, fill=CARD)
            title_fill = (130, 140, 155) if row["claimed"] else (255, 255, 255)
            draw.text(
                (64, y + 24),
                fit(draw, row["text"], font(22, True), 430),
                font=font(22, True),
                fill=title_fill,
                anchor="lm",
            )
            bar = (64, y + 48, 494, y + 54)
            draw.rounded_rectangle(bar, 3, fill=LINE)
            share = min(1, row["progress"] / row["goal"])
            if share:
                draw.rounded_rectangle(
                    (bar[0], bar[1], bar[0] + (bar[2] - bar[0]) * share, bar[3]),
                    3,
                    fill=GOLD if done else CYAN,
                )
            draw.text(
                (514, y + 51),
                f"{row['progress']}/{row['goal']}",
                font=font(16, True),
                fill=MUTED,
                anchor="lm",
            )
            if row["claimed"]:
                _tracked(draw, (width - 64, y + 35), "RESGATADA", 14, (120, 130, 145), 4, "rm")
            else:
                amount = f"{row['reward']:,}".replace(",", ".")
                right = width - 64
                draw.text(
                    (right, y + 35),
                    amount,
                    font=font(24, True),
                    fill=GOLD if done else (255, 255, 255),
                    anchor="rm",
                )
                text_width = draw.textlength(amount, font=font(24, True))
                if coin is not None:
                    image.alpha_composite(coin, (int(right - text_width - 40), y + 20))
                if done:
                    _tracked(draw, (right - text_width - 54, y + 35), "PRONTA", 13, GOLD, 4, "rm")
            y += 84
    return to_png(image)


def render_season(table: list[dict[str, Any]], title: str) -> BytesIO:
    width = 1000
    shown = table[:10]
    height = 560 + 58 * max(1, len(shown)) + 30
    image = Image.new("RGBA", (width, height), (*BACKGROUND, 255))
    image.alpha_composite(glow(image.size, (width / 2, 300), 300, GOLD, 45))
    _header(image, "TEMPORADA", f"{title} · soma dos campeonatos diários", "temporada", GOLD)
    draw = ImageDraw.Draw(image)
    if not shown:
        text_center(
            draw, (width / 2, 330), "Nenhum campeonato terminado neste mês ainda.", 24, False, MUTED
        )
        return to_png(image)

    # Pódio: 2º, 1º e 3º.
    slots = (
        (1, width / 2 - 280, 190, SILVER),
        (0, width / 2, 220, GOLD),
        (2, width / 2 + 280, 170, BRONZE),
    )
    for index, center, size, medal_color in slots:
        if index >= len(shown):
            continue
        team = shown[index]
        badge = emblem(team, size - 50)
        top = 410 - badge.height
        image.alpha_composite(badge, (int(center - badge.width / 2), int(top)))
        draw.ellipse((center - 18, top - 10, center + 18, top + 26), fill=medal_color)
        text_center(draw, (center, top + 8), str(index + 1), 20, True, BACKGROUND)
        name = fit(draw, team["team_name"], font(24, True), 250)
        text_center(draw, (center, 440), name, 24)
        text_center(
            draw,
            (center, 474),
            f"{team['points']} pts · {team['titles']} título{'s' if team['titles'] != 1 else ''}",
            18,
            True,
            medal_color,
        )

    y = 530
    columns = (
        (560, "J"),
        (620, "V"),
        (680, "E"),
        (740, "D"),
        (810, "SG"),
        (880, "TÍT"),
        (950, "PTS"),
    )
    for x, label in columns:
        _tracked(draw, (x, y), label, 13, MUTED, 2, "mm")
    y += 30
    for rank, team in enumerate(shown, start=1):
        if rank % 2:
            draw.rounded_rectangle((30, y - 2, width - 30, y + 50), 10, fill=CARD)
        text_center(draw, (60, y + 24), str(rank), 20, True, GOLD if rank == 1 else MUTED)
        mini = emblem(team, 40)
        image.alpha_composite(mini, (90, y + 4))
        draw.text(
            (146, y + 24),
            fit(draw, team["team_name"], font(21, True), 380),
            font=font(21, True),
            fill="white",
            anchor="lm",
        )
        values = (
            team["played"],
            team["wins"],
            team["draws"],
            team["losses"],
            team["gf"] - team["ga"],
            team["titles"],
            team["points"],
        )
        for (x, label), value in zip(columns, values):
            text_center(
                draw,
                (x, y + 24),
                str(value),
                22 if label == "PTS" else 19,
                label == "PTS",
                "white" if label == "PTS" else (205, 212, 222),
            )
        y += 58
    draw.rectangle((0, height - 6, width, height), fill=mix(GOLD, BACKGROUND, 0.3))
    return to_png(image)
