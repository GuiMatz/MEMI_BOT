"""Quadros em imagem: missões (`mm!missoes`) e tabela do Pintos Corridos (`mm!tabela`).

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


FINALIST = (88, 196, 132)
FORM_GAMES = 4  # bolinhas da coluna ÚLT. da tabela


def render_league_table(table: list[dict[str, Any]], title: str, subtitle: str) -> BytesIO:
    """Tabela do Pintos Corridos: todos os times (até 16), os dois primeiros em verde
    (vão para a final), com jogos, V-E-D, saldo, pontos e os últimos 4 resultados."""
    width = 1000
    row_height = 60
    height = 160 + 40 + row_height * max(1, len(table)) + 30
    image = Image.new("RGBA", (width, height), (*BACKGROUND, 255))
    image.alpha_composite(glow(image.size, (width - 140, 40), 260, GOLD, 40))
    _header(image, title, subtitle, "temporada", GOLD)
    draw = ImageDraw.Draw(image)
    y = 168
    columns = ((560, "J"), (650, "V-E-D"), (760, "SG"), (850, "ÚLT."), (940, "PTS"))
    for x, label in columns:
        text_center(draw, (x, y), label, 20, True, MUTED)
    y += 26
    form_colors = {"V": (88, 196, 132), "E": (150, 158, 172), "D": (220, 90, 90)}
    for rank, team in enumerate(table, start=1):
        finalist = rank <= 2
        if finalist:
            draw.rounded_rectangle(
                (26, y, width - 26, y + row_height - 6), 12, fill=mix(FINALIST, BACKGROUND, 0.82)
            )
        elif rank % 2:
            draw.rounded_rectangle((26, y, width - 26, y + row_height - 6), 12, fill=CARD)
        middle = y + (row_height - 6) / 2
        text_center(draw, (60, middle), str(rank), 26, True, FINALIST if finalist else MUTED)
        mini = emblem(team, 40)
        image.alpha_composite(mini, (92, int(middle - 20)))
        draw.text(
            (146, middle),
            fit(draw, team["team_name"], font(26, True), 360),
            font=font(26, True),
            fill="white",
            anchor="lm",
        )
        values = (
            str(team["played"]),
            f"{team['wins']}-{team['draws']}-{team['losses']}",
            f"{team['gf'] - team['ga']:+d}",
        )
        for (x, _), value in zip(columns, values):
            text_center(draw, (x, middle), value, 24, True, (205, 212, 222))
        # Os últimos 4 jogos, do mais antigo (esquerda) ao mais recente (direita).
        for index, outcome in enumerate(team.get("form", [])[-FORM_GAMES:]):
            x = 814 + index * 24
            draw.ellipse((x - 8, middle - 8, x + 8, middle + 8), fill=form_colors[outcome])
        text_center(draw, (940, middle), str(team["points"]), 30, True, "white")
        y += row_height
    draw.rectangle((0, height - 8, width, height), fill=mix(GOLD, BACKGROUND, 0.3))
    return to_png(image)
