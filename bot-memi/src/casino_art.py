"""Animações do cassino em GIF: moeda, caça-níquel e roleta.

Mesmo estilo dos outros GIFs do bot (fundo escuro, dourado, formas chapadas) e a mesma
regra: o GIF para no último quadro, mostrando o resultado.
"""

from __future__ import annotations

import math
import random

from PIL import Image, ImageDraw

from src.casino import RED_NUMBERS, ROULETTE_WHEEL, SLOT_SYMBOLS
from src.crate_animation import BACKGROUND, GOLD, HOLD_MS, _gif, _tracked
from src.visual import font, mix, star

RED = (214, 64, 64)
BLACK = (36, 40, 52)
WIN = (88, 196, 120)
LOSE = (214, 84, 84)
SUPERSAMPLE = 2


def _ease_out(t: float, power: int = 3) -> float:
    return 1 - (1 - t) ** power


# --- Moeda ----------------------------------------------------------------------------


def _coin_face(side: str, size: int) -> Image.Image:
    big = size * SUPERSAMPLE
    face = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    draw = ImageDraw.Draw(face)
    draw.ellipse((0, 0, big - 1, big - 1), fill=(*GOLD, 255))
    inset = big * 0.08
    dark = mix(GOLD, (90, 60, 10), 0.55)
    draw.ellipse((inset, inset, big - inset, big - inset), outline=(*dark, 255), width=big // 40)
    c = big / 2
    if side == "coroa":
        w, h = big * 0.46, big * 0.28
        top, bottom = c - h * 0.7, c + h * 0.6
        draw.polygon(
            [
                (c - w / 2, bottom),
                (c - w / 2, top + h * 0.35),
                (c - w / 4, top + h * 0.75),
                (c, top),
                (c + w / 4, top + h * 0.75),
                (c + w / 2, top + h * 0.35),
                (c + w / 2, bottom),
            ],
            fill=(*dark, 255),
        )
    else:
        draw.text((c, c), "M", font=font(int(big * 0.42), True), fill=(*dark, 255), anchor="mm")
    return face.resize((size, size), Image.LANCZOS)


def coin_gif(side: str, headline: str, won: bool) -> bytes:
    """A moeda gira (achata e volta), desacelera e cai em `side`."""
    width, height = 420, 400
    size = 220
    faces = {name: _coin_face(name, size) for name in ("cara", "coroa")}
    other = "coroa" if side == "cara" else "cara"
    frames, durations = [], []
    turns = 26
    for index in range(turns):
        t = index / (turns - 1)
        angle = _ease_out(t) * math.pi * 9  # 4,5 voltas: termina mostrando `side`
        squash = abs(math.cos(angle))
        showing = side if math.cos(angle) >= 0 else other
        if index == turns - 1:
            squash, showing = 1.0, side
        image = Image.new("RGBA", (width, height), (*BACKGROUND, 255))
        _tracked(ImageDraw.Draw(image), (width / 2, 32), "CARA OU COROA", 20, (*GOLD, 255), 6)
        coin = faces[showing].resize((max(2, int(size * squash)), size), Image.LANCZOS)
        lift = int(40 * math.sin(math.pi * t))
        image.alpha_composite(coin, ((width - coin.width) // 2, 80 - lift))
        frames.append(image)
        durations.append(40 + int(80 * t**2))
    for index in range(10):
        image = frames[-1].copy()
        alpha = min(255, 60 + 40 * index)
        layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        _tracked(
            ImageDraw.Draw(layer),
            (width / 2, height - 50),
            headline,
            26,
            (*(WIN if won else LOSE), alpha),
            6,
        )
        image.alpha_composite(layer)
        frames.append(image)
        durations.append(60)
    durations[-1] = HOLD_MS
    return _gif([frame.convert("RGB") for frame in frames], durations, loop=False)


# --- Caça-níquel ----------------------------------------------------------------------

SYMBOL_COLORS = {
    "cereja": (214, 64, 76),
    "limao": (236, 210, 80),
    "sino": (230, 181, 74),
    "estrela": (120, 170, 255),
    "diamante": (110, 220, 230),
    "sete": (226, 70, 70),
}


def _symbol(name: str, size: int) -> Image.Image:
    big = size * SUPERSAMPLE
    layer = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    color = (*SYMBOL_COLORS[name], 255)
    c, r = big / 2, big * 0.36
    if name == "cereja":
        for dx in (-0.45, 0.4):
            x, y = c + dx * r, c + r * 0.45
            draw.ellipse((x - r * 0.42, y - r * 0.42, x + r * 0.42, y + r * 0.42), fill=color)
        draw.line(
            (c - 0.45 * r, c + 0.05 * r, c + 0.1 * r, c - r * 0.9),
            fill=(90, 160, 80, 255),
            width=big // 22,
        )
        draw.line(
            (c + 0.4 * r, c + 0.05 * r, c + 0.1 * r, c - r * 0.9),
            fill=(90, 160, 80, 255),
            width=big // 22,
        )
    elif name == "limao":
        draw.ellipse((c - r, c - r * 0.7, c + r, c + r * 0.7), fill=color)
    elif name == "sino":
        draw.pieslice((c - r * 0.8, c - r, c + r * 0.8, c + r * 0.6), 180, 360, fill=color)
        draw.rectangle((c - r * 0.8, c - r * 0.2, c + r * 0.8, c + r * 0.5), fill=color)
        draw.rectangle((c - r, c + r * 0.45, c + r, c + r * 0.65), fill=color)
        draw.ellipse((c - r * 0.18, c + r * 0.6, c + r * 0.18, c + r * 0.95), fill=color)
    elif name == "estrela":
        draw.polygon(star(c, c, r * 1.05, r * 0.45), fill=color)
    elif name == "diamante":
        draw.polygon(
            [
                (c - r, c - r * 0.25),
                (c - r * 0.5, c - r * 0.75),
                (c + r * 0.5, c - r * 0.75),
                (c + r, c - r * 0.25),
                (c, c + r),
            ],
            fill=color,
        )
    else:  # sete
        draw.text((c, c), "7", font=font(int(big * 0.78), True), fill=color, anchor="mm")
    return layer.resize((size, size), Image.LANCZOS)


def slots_gif(reels: tuple[str, str, str], headline: str, won: bool) -> bytes:
    """Três rolos girando, que param um de cada vez em `reels`."""
    width, height = 480, 300
    cell = 96
    names = list(SLOT_SYMBOLS)
    art = {name: _symbol(name, 72) for name in names}
    rng = random.Random("|".join(reels))
    strips = [[rng.choice(names) for _ in range(30)] + [reel] for reel in reels]
    stops = (18, 26, 34)
    total = stops[-1] + 1
    left = (width - 3 * cell - 2 * 16) // 2
    top = 80
    frames, durations = [], []
    for index in range(total + 12):
        image = Image.new("RGBA", (width, height), (*BACKGROUND, 255))
        draw = ImageDraw.Draw(image)
        _tracked(draw, (width / 2, 34), "CAÇA-NÍQUEL", 20, (*GOLD, 255), 6)
        for column, strip in enumerate(strips):
            x = left + column * (cell + 16)
            draw.rounded_rectangle((x, top, x + cell, top + cell * 1.4), 14, fill=(22, 27, 38, 255))
            if index >= stops[column]:
                offset = 0.0
                symbol_index = len(strip) - 1
            else:
                progress = index / stops[column]
                position = _ease_out(progress, 2) * (len(strip) - 1)
                symbol_index = int(position)
                offset = position - symbol_index
            window = Image.new("RGBA", (cell, int(cell * 1.4)), (0, 0, 0, 0))
            for shift in (-1, 0, 1):
                name = strip[(symbol_index + shift) % len(strip)]
                y = int((cell * 1.4 - 72) / 2 + (shift - offset) * cell * 1.2)
                window.alpha_composite(art[name], ((cell - 72) // 2, y))
            image.alpha_composite(window, (x, top))
        if index > total and won:
            pulse = 0.5 + 0.5 * math.sin(index)
            draw.rounded_rectangle(
                (left - 8, top - 8, left + 3 * cell + 40, top + cell * 1.4 + 8),
                18,
                outline=(*mix(GOLD, (255, 255, 255), pulse * 0.4), 255),
                width=4,
            )
        if index >= total:
            _tracked(
                draw, (width / 2, height - 36), headline, 22, (*(WIN if won else LOSE), 255), 5
            )
        frames.append(image)
        durations.append(55)
    durations[-1] = HOLD_MS
    return _gif([frame.convert("RGB") for frame in frames], durations, loop=False)


# --- Roleta ---------------------------------------------------------------------------


def _roulette_face(radius: int) -> Image.Image:
    big = radius * 2 * SUPERSAMPLE
    face = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    draw = ImageDraw.Draw(face)
    step = 360 / len(ROULETTE_WHEEL)
    for index, number in enumerate(ROULETTE_WHEEL):
        start = -90 + index * step
        color = RED if number in RED_NUMBERS else BLACK
        draw.pieslice((0, 0, big - 1, big - 1), start, start + step, fill=(*color, 255))
    center = big / 2
    label = font(int(big * 0.035), True)
    for index, number in enumerate(ROULETTE_WHEEL):
        angle = math.radians(-90 + (index + 0.5) * step)
        x = center + center * 0.84 * math.cos(angle)
        y = center + center * 0.84 * math.sin(angle)
        draw.text((x, y), str(number), font=label, fill=(240, 240, 240, 255), anchor="mm")
    hub = big * 0.3
    draw.ellipse(
        (center - hub, center - hub, center + hub, center + hub),
        fill=(*BACKGROUND, 255),
        outline=(*GOLD, 255),
        width=6,
    )
    draw.ellipse((0, 0, big - 1, big - 1), outline=(*GOLD, 255), width=8)
    return face.resize((radius * 2, radius * 2), Image.LANCZOS)


def roulette_gif(number: int, headline: str, won: bool) -> bytes:
    """A roda gira e para com o número sorteado na seta; o número aparece no centro."""
    width, height, radius = 460, 510, 190
    face = _roulette_face(radius)
    step = 360 / len(ROULETTE_WHEEL)
    slot = ROULETTE_WHEEL.index(number)
    final = 5 * 360 + (-(slot + 0.5) * step) % 360
    center = (width // 2, 255)
    color = RED if number in RED_NUMBERS else (200, 205, 215)
    frames, durations = [], []
    spin = 50
    for index in range(spin + 14):
        t = min(1.0, index / (spin - 1))
        angle = final * _ease_out(t, 4)
        image = Image.new("RGBA", (width, height), (*BACKGROUND, 255))
        draw = ImageDraw.Draw(image)
        _tracked(draw, (width / 2, 30), "ROLETA", 20, (*GOLD, 255), 8)
        image.alpha_composite(
            face.rotate(-angle, Image.BICUBIC), (center[0] - radius, center[1] - radius)
        )
        tip = center[1] - radius + 14
        draw.polygon(
            [(center[0] - 14, tip - 32), (center[0] + 14, tip - 32), (center[0], tip)],
            fill=(255, 255, 255, 255),
        )
        if index >= spin:
            draw.text(center, str(number), font=font(64, True), fill=(*color, 255), anchor="mm")
            _tracked(
                draw, (width / 2, height - 30), headline, 20, (*(WIN if won else LOSE), 255), 5
            )
        frames.append(image)
        durations.append(40 + int(70 * t**3) if index < spin else 80)
    durations[-1] = HOLD_MS
    return _gif([frame.convert("RGB") for frame in frames], durations, loop=False)
