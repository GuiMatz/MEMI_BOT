"""Quadros em imagem: missões (`mm!missoes`) e temporada mensal (`mm!temporada`).

O Discord mostra a imagem do embed com ~450 px de largura: o texto aqui é grande (26 px
ou mais nas linhas) para continuar legível nesse tamanho.
"""

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
        return opened.convert("RGBA").resize((40, 40), Image.LANCZOS)


def _header(image: Image.Image, title: str, subtitle: str, art: str, accent) -> None:
    draw = ImageDraw.Draw(image)
    medal = artwork(art)
    if medal is not None:
        image.alpha_composite(medal.resize((88, 88), Image.LANCZOS), (36, 26))
    draw.text((140, 54), title, font=font(40, True), fill="white", anchor="lm")
    draw.text(
        (140, 98),
        fit(draw, subtitle, font(26), image.width - 180),
        font=font(26),
        fill=MUTED,
        anchor="lm",
    )
    draw.line((36, 138, image.width - 36, 138), fill=(*accent, 255), width=3)


def render_missions(rows: list[dict[str, Any]], owner: str) -> BytesIO:
    width = 900
    row_height = 108
    sections = [("DIÁRIAS", "diaria"), ("SEMANAIS", "semanal")]
    height = (
        160
        + sum(
            52 + row_height * sum(1 for row in rows if row["scope"] == scope)
            for _, scope in sections
        )
        + 16
    )
    image = Image.new("RGBA", (width, height), (*BACKGROUND, 255))
    image.alpha_composite(glow(image.size, (width - 120, 40), 220, CYAN, 40))
    _header(image, "MISSÕES", f"de {owner}", "missoes", CYAN)
    draw = ImageDraw.Draw(image)
    y = 160
    coin = _coin()
    for label, scope in sections:
        draw.text((36, y + 22), label, font=font(22, True), fill=MUTED, anchor="lm")
        y += 52
        for row in (row for row in rows if row["scope"] == scope):
            done = row["progress"] >= row["goal"]
            draw.rounded_rectangle((36, y, width - 36, y + row_height - 14), 16, fill=CARD)
            title_fill = (130, 140, 155) if row["claimed"] else (255, 255, 255)
            draw.text(
                (60, y + 32),
                fit(draw, row["text"], font(30, True), 520),
                font=font(30, True),
                fill=title_fill,
                anchor="lm",
            )
            bar = (60, y + 66, 470, y + 76)
            draw.rounded_rectangle(bar, 5, fill=LINE)
            share = min(1, row["progress"] / row["goal"])
            if share:
                draw.rounded_rectangle(
                    (bar[0], bar[1], bar[0] + (bar[2] - bar[0]) * share, bar[3]),
                    5,
                    fill=GOLD if done else CYAN,
                )
            draw.text(
                (488, y + 71),
                f"{row['progress']}/{row['goal']}",
                font=font(24, True),
                fill=MUTED,
                anchor="lm",
            )
            right = width - 60
            if row["claimed"]:
                draw.text(
                    (right, y + 47),
                    "RESGATADA",
                    font=font(24, True),
                    fill=(120, 130, 145),
                    anchor="rm",
                )
            else:
                amount = f"{row['reward']:,}".replace(",", ".")
                draw.text(
                    (right, y + 38),
                    amount,
                    font=font(34, True),
                    fill=GOLD if done else (255, 255, 255),
                    anchor="rm",
                )
                text_width = draw.textlength(amount, font=font(34, True))
                if coin is not None:
                    image.alpha_composite(coin, (int(right - text_width - 50), y + 18))
                if done:
                    draw.text(
                        (right, y + 74), "PRONTA", font=font(20, True), fill=GOLD, anchor="rm"
                    )
            y += row_height
    return to_png(image)


def render_season(table: list[dict[str, Any]], title: str) -> BytesIO:
    width = 1000
    shown = table[:8]
    row_height = 70
    height = 600 + row_height * max(1, len(shown)) + 30
    image = Image.new("RGBA", (width, height), (*BACKGROUND, 255))
    image.alpha_composite(glow(image.size, (width / 2, 320), 300, GOLD, 45))
    _header(image, "TEMPORADA", title, "temporada", GOLD)
    draw = ImageDraw.Draw(image)
    if not shown:
        text_center(draw, (width / 2, 340), "Nenhum campeonato terminado ainda.", 30, False, MUTED)
        return to_png(image)

    # Pódio: 2º, 1º e 3º.
    slots = (
        (1, width / 2 - 300, 170, SILVER),
        (0, width / 2, 210, GOLD),
        (2, width / 2 + 300, 150, BRONZE),
    )
    for index, center, size, medal_color in slots:
        if index >= len(shown):
            continue
        team = shown[index]
        badge = emblem(team, size)
        top = 410 - badge.height
        image.alpha_composite(badge, (int(center - badge.width / 2), int(top)))
        draw.ellipse((center - 22, top - 14, center + 22, top + 30), fill=medal_color)
        text_center(draw, (center, top + 8), str(index + 1), 26, True, BACKGROUND)
        name = fit(draw, team["team_name"], font(32, True), 280)
        text_center(draw, (center, 450), name, 32)
        text_center(draw, (center, 492), f"{team['points']} pts", 28, True, medal_color)

    y = 556
    columns = ((640, "V-E-D"), (770, "SG"), (850, "TÍT"), (935, "PTS"))
    for x, label in columns:
        text_center(draw, (x, y), label, 20, True, MUTED)
    y += 28
    for rank, team in enumerate(shown, start=1):
        if rank % 2:
            draw.rounded_rectangle((26, y, width - 26, y + row_height - 6), 12, fill=CARD)
        middle = y + (row_height - 6) / 2
        text_center(draw, (62, middle), str(rank), 28, True, GOLD if rank == 1 else MUTED)
        mini = emblem(team, 48)
        image.alpha_composite(mini, (96, int(middle - 24)))
        draw.text(
            (160, middle),
            fit(draw, team["team_name"], font(28, True), 380),
            font=font(28, True),
            fill="white",
            anchor="lm",
        )
        values = (
            f"{team['wins']}-{team['draws']}-{team['losses']}",
            f"{team['gf'] - team['ga']:+d}",
            str(team["titles"]),
            str(team["points"]),
        )
        for (x, label), value in zip(columns, values):
            text_center(
                draw,
                (x, middle),
                value,
                32 if label == "PTS" else 26,
                True,
                "white" if label == "PTS" else (205, 212, 222),
            )
        y += row_height
    draw.rectangle((0, height - 8, width, height), fill=mix(GOLD, BACKGROUND, 0.3))
    return to_png(image)
