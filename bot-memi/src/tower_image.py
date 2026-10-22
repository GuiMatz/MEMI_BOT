"""Desenho do Centro de Treinamento: o prédio com os andares prontos acesos."""

from __future__ import annotations

import random
from io import BytesIO

from PIL import ImageDraw

from src.perks import TOWER_FLOORS, TOWER_SHORT
from src.tower import TowerStatus
from src.visual import (
    MUTED,
    apply_grain,
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
    vignette,
)

WIDTH, HEIGHT = 900, 1240
FLOOR_HEIGHT = 66
LEFT, RIGHT = 120, 470
GROUND = 1130
WINDOW_LIT = (255, 206, 110)
FACADE = (64, 80, 108)


def _thousands(value: int) -> str:
    return f"{value:,}".replace(",", ".")


def render_tower(status: TowerStatus, team: dict | None = None) -> BytesIO:
    color = rgb((team or {}).get("color") or "#14B8A6")
    construction = status.construction
    image = vertical_gradient((WIDTH, HEIGHT), (12, 16, 30), (40, 58, 88))
    sky = ImageDraw.Draw(image)
    generator = random.Random(12)
    for _ in range(70):
        x, y = generator.randint(0, WIDTH), generator.randint(0, 700)
        size = generator.choice((1, 1, 2))
        sky.ellipse((x, y, x + size, y + size), fill=(255, 255, 255, generator.randint(90, 200)))
    image.alpha_composite(glow((WIDTH, HEIGHT), (300, 620), 330, mix(color, WINDOW_LIT, 0.5), 60))

    draw = ImageDraw.Draw(image)
    built_now = sum(1 for floor in TOWER_FLOORS if status.built_in_current(floor))
    top = GROUND - FLOOR_HEIGHT * len(TOWER_FLOORS)

    # Telhado, mastro e bandeira na cor do time.
    draw.polygon(
        [(LEFT - 14, top), (RIGHT + 14, top), (RIGHT - 30, top - 46), (LEFT + 30, top - 46)],
        fill=(40, 50, 68),
    )
    pole_x = (LEFT + RIGHT) // 2
    draw.rectangle((pole_x - 3, top - 150, pole_x + 3, top - 46), fill=(200, 205, 215))
    wave = [
        (pole_x + 3, top - 150),
        (pole_x + 90, top - 138),
        (pole_x + 80, top - 112),
        (pole_x + 3, top - 100),
    ]
    draw.polygon(wave, fill=color if built_now == 12 else shade(color, 0.6))
    if built_now == 12:
        image.alpha_composite(glow((WIDTH, HEIGHT), (pole_x + 40, top - 125), 70, color, 120))
        draw = ImageDraw.Draw(image)

    for floor in TOWER_FLOORS:
        y1 = GROUND - FLOOR_HEIGHT * (floor - 1)
        y0 = y1 - FLOOR_HEIGHT
        times = status.floors.get(floor, 0)
        # Aceso = efeito ativo (de qualquer construção); "obra" = falta nesta construção.
        built = times > 0
        pending = built and not status.built_in_current(floor)
        if built:
            draw.rectangle((LEFT, y0, RIGHT, y1), fill=FACADE if floor % 2 else shade(FACADE, 0.9))
            draw.rectangle((LEFT, y1 - 6, RIGHT, y1), fill=shade(FACADE, 0.7))
            for index in range(5):
                wx = LEFT + 80 + index * 52
                lit = generator.random() < 0.85
                draw.rounded_rectangle(
                    (wx, y0 + 16, wx + 32, y0 + 44),
                    4,
                    fill=WINDOW_LIT if lit else (90, 100, 120),
                )
        else:
            draw.rectangle((LEFT, y0, RIGHT, y1), outline=(110, 125, 150), width=2)
            for x in range(LEFT, RIGHT, 44):
                draw.line((x, y1, x + 44, y0), fill=(80, 92, 115), width=2)
            draw.line(
                (LEFT, y0 + FLOOR_HEIGHT // 2, RIGHT, y0 + FLOOR_HEIGHT // 2),
                fill=(80, 92, 115),
                width=2,
            )
        # Número do andar num círculo na fachada.
        badge = color if built else (52, 60, 78)
        draw.ellipse(
            (LEFT + 16, y0 + 12, LEFT + 58, y0 + 54),
            fill=badge,
            outline="white" if built else (110, 125, 150),
            width=2,
        )
        text_center(draw, (LEFT + 37, y0 + 33), str(floor), 22, fill="white" if built else MUTED)
        # Rótulo do efeito ao lado do prédio.
        label = fit(draw, TOWER_SHORT[floor], font(22, True), 330)
        draw.line(
            (RIGHT + 8, y0 + 33, RIGHT + 26, y0 + 33),
            fill=WINDOW_LIT if built else (90, 100, 120),
            width=3,
        )
        draw.text(
            (RIGHT + 36, y0 + 33),
            label,
            font=font(22, True),
            fill="white" if built else (120, 132, 150),
            anchor="lm",
        )
        tag_x = RIGHT + 46 + draw.textlength(label, font=font(22, True))
        if times > 1:
            tag_x += (
                2
                * pill(draw, (tag_x + 26, y0 + 33), f"×{times}", 16, WINDOW_LIT, padding=(10, 5))
                / 2
                + 8
            )
        if pending:
            pill(draw, (tag_x + 30, y0 + 33), "obra", 16, (120, 130, 150), padding=(10, 5))

    # Chão e grama.
    draw.rectangle((0, GROUND, WIDTH, HEIGHT), fill=(18, 40, 30))
    draw.rectangle((0, GROUND, WIDTH, GROUND + 8), fill=(46, 120, 70))
    draw.rectangle((LEFT - 30, GROUND, RIGHT + 30, GROUND + 14), fill=(70, 78, 94))

    # Cabeçalho.
    title_team = (team or {}).get("team_name")
    text_center(draw, (WIDTH // 2, 54), "CENTRO DE TREINAMENTO", 40)
    if title_team:
        text_center(draw, (WIDTH // 2, 100), fit(draw, title_team, font(24), 700), 24, False, MUTED)
    chips = (
        (f"Construção nº {construction}", (235, 240, 245)),
        (f"{built_now}/12 andares", WINDOW_LIT if built_now else (200, 205, 215)),
        (f"Próximo: {_thousands(status.next_price)}", (160, 220, 200)),
    )
    x = 150
    for text, fill in chips:
        width = draw.textlength(text, font=font(20, True)) + 36
        pill(draw, (x + width / 2 - 18, 150), text, 20, fill)
        x += width + 12

    text_center(
        draw,
        (WIDTH // 2, GROUND + 60),
        f"Investido: {_thousands(status.spent)} Memilets · mm!destruir devolve tudo",
        20,
        False,
        (170, 190, 175),
    )
    image = apply_grain(image, 6)
    vignette(image, 90)
    return to_png(image)
