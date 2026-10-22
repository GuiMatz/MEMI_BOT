"""Desenho do Centro de Treinamento: o prédio à esquerda e a lista dos andares à direita.

A imagem é horizontal (16:9) para o Discord mostrá-la grande no embed: imagens altas
são encolhidas e o texto fica ilegível. Os rótulos ficam na grade da direita, em letra
grande; o prédio é a parte visual, com os andares ativos acesos.
"""

from __future__ import annotations

import random
from io import BytesIO

from PIL import ImageDraw

from src.perks import TOWER_FLOORS, TOWER_SHORT
from src.tower import TowerStatus
from src.visual import (
    MUTED,
    fit,
    font,
    glow,
    mix,
    pill,
    rgb,
    shade,
    text_center,
    to_png,
    vertical_gradient,
)

WIDTH, HEIGHT = 1280, 720
WINDOW_LIT = (255, 206, 110)
FACADE = (64, 80, 108)
GHOST = (88, 100, 124)
# Prédio
LEFT, RIGHT = 70, 370
GROUND = 650
FLOOR_HEIGHT = 40
# Grade de andares
GRID_LEFT = 440
GRID_TOP = 176
ROW_HEIGHT = 74
COLUMN_WIDTH = 410


def _thousands(value: int) -> str:
    return f"{value:,}".replace(",", ".")


def render_tower(status: TowerStatus, team: dict | None = None) -> BytesIO:
    color = rgb((team or {}).get("color") or "#14B8A6")
    construction = status.construction
    built_now = sum(1 for floor in TOWER_FLOORS if status.built_in_current(floor))
    image = vertical_gradient((WIDTH, HEIGHT), (12, 16, 28), (24, 34, 54))
    sky = ImageDraw.Draw(image)
    generator = random.Random(12)
    for _ in range(50):
        x, y = generator.randint(0, 400), generator.randint(0, 420)
        sky.ellipse((x, y, x + 2, y + 2), fill=(255, 255, 255, generator.randint(70, 180)))
    image.alpha_composite(glow((WIDTH, HEIGHT), (220, 420), 260, mix(color, WINDOW_LIT, 0.5), 50))
    draw = ImageDraw.Draw(image)

    # Prédio: telhado, mastro e bandeira na cor do time.
    top = GROUND - FLOOR_HEIGHT * len(TOWER_FLOORS)
    draw.polygon(
        [(LEFT - 10, top), (RIGHT + 10, top), (RIGHT - 24, top - 34), (LEFT + 24, top - 34)],
        fill=(40, 50, 68),
    )
    pole = (LEFT + RIGHT) // 2
    draw.rectangle((pole - 2, top - 110, pole + 2, top - 34), fill=(200, 205, 215))
    draw.polygon(
        [
            (pole + 2, top - 110),
            (pole + 66, top - 101),
            (pole + 58, top - 82),
            (pole + 2, top - 74),
        ],
        fill=color if built_now == 12 else shade(color, 0.6),
    )
    for floor in TOWER_FLOORS:
        y1 = GROUND - FLOOR_HEIGHT * (floor - 1)
        y0 = y1 - FLOOR_HEIGHT
        active = status.floors.get(floor, 0) > 0
        if active:
            draw.rectangle((LEFT, y0, RIGHT, y1), fill=FACADE if floor % 2 else shade(FACADE, 0.9))
            draw.rectangle((LEFT, y1 - 4, RIGHT, y1), fill=shade(FACADE, 0.7))
            for index in range(5):
                wx = LEFT + 70 + index * 46
                lit = generator.random() < 0.85
                draw.rounded_rectangle(
                    (wx, y0 + 9, wx + 28, y0 + 28), 3, fill=WINDOW_LIT if lit else (90, 100, 120)
                )
        else:
            draw.rectangle((LEFT, y0, RIGHT, y1), outline=GHOST, width=2)
            for x in range(LEFT, RIGHT, 30):
                draw.line((x, y1, x + 30, y0), fill=(70, 82, 104), width=1)
        badge = color if active else (46, 54, 72)
        draw.ellipse((LEFT + 12, y0 + 6, LEFT + 40, y0 + 34), fill=badge)
        text_center(draw, (LEFT + 26, y0 + 20), str(floor), 15, fill="white" if active else MUTED)
    draw.rectangle((0, GROUND, 410, HEIGHT), fill=(18, 40, 30))
    draw.rectangle((0, GROUND, 410, GROUND + 6), fill=(46, 120, 70))
    draw.rectangle((LEFT - 24, GROUND, RIGHT + 24, GROUND + 10), fill=(70, 78, 94))

    # Painel da direita.
    draw.rectangle((410, 0, WIDTH, HEIGHT), fill=(14, 18, 25, 235))
    draw.line((410, 0, 410, HEIGHT), fill=(*color, 255), width=3)
    draw.text(
        (GRID_LEFT, 54), "CENTRO DE TREINAMENTO", font=font(38, True), fill="white", anchor="lm"
    )
    team_name = (team or {}).get("team_name")
    if team_name:
        draw.text(
            (GRID_LEFT, 96),
            fit(draw, team_name, font(22), 760),
            font=font(22),
            fill=MUTED,
            anchor="lm",
        )
    chips = (
        (f"Construção nº {construction}", (235, 240, 245)),
        (f"{built_now}/12 andares", WINDOW_LIT if built_now else (200, 205, 215)),
        (f"Próximo: {_thousands(status.next_price)}", (160, 220, 200)),
    )
    x = GRID_LEFT
    for text, fill in chips:
        width = draw.textlength(text, font=font(20, True)) + 36
        pill(draw, (x + width / 2, 136), text, 20, fill)
        x += width + 12

    for floor in TOWER_FLOORS:
        column, row = (floor - 1) // 6, (floor - 1) % 6
        x = GRID_LEFT + column * COLUMN_WIDTH
        y = GRID_TOP + row * ROW_HEIGHT
        times = status.floors.get(floor, 0)
        active = times > 0
        pending = active and not status.built_in_current(floor)
        draw.rounded_rectangle(
            (x, y + 6, x + COLUMN_WIDTH - 20, y + ROW_HEIGHT - 6),
            14,
            fill=(28, 36, 50) if active else (20, 25, 34),
            outline=(*color, 255) if active and not pending else None,
            width=2,
        )
        draw.ellipse((x + 16, y + 19, x + 52, y + 55), fill=color if active else (46, 54, 72))
        text_center(draw, (x + 34, y + 37), str(floor), 19, fill="white" if active else MUTED)
        room = COLUMN_WIDTH - (170 if pending or times > 1 else 110)
        label = fit(draw, TOWER_SHORT[floor], font(25, True), room)
        draw.text(
            (x + 68, y + 37),
            label,
            font=font(25, True),
            fill="white" if active else (125, 136, 154),
            anchor="lm",
        )
        tag_x = x + COLUMN_WIDTH - 40
        if pending:
            pill(draw, (tag_x - 22, y + 37), "obra", 16, (120, 130, 150), padding=(10, 5))
        elif times > 1:
            pill(draw, (tag_x - 14, y + 37), f"×{times}", 16, WINDOW_LIT, padding=(10, 5))

    text_center(
        draw,
        (WIDTH / 2 + 205, HEIGHT - 34),
        f"Investido: {_thousands(status.spent)} Memilets · mm!destruir devolve tudo",
        19,
        False,
        (170, 190, 175),
    )
    return to_png(image)
