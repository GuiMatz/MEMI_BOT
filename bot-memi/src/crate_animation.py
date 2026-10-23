"""Animações das caixas e do pacote de jogador em GIF.

A revelação começa com o pacote tremendo e a aba voando (a luz já tem a cor da raridade),
mostra os prêmios (ou a carta do pacote), faz um brilho e **para no último quadro** (GIF
sem loop e último quadro longo), para a pessoa olhar sem a animação reiniciar.

O pacote é a arte `assets/ilustracoes/pacote.png` (corpo) e `pacote_topo.png` (aba);
a animação é montada aqui com Pillow e guardada em memória.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from functools import lru_cache
from io import BytesIO
from typing import Any

from PIL import Image, ImageChops, ImageDraw, ImageFilter

from src.art import artwork
from src.visual import contain, font, glow, load_picture, mix, star

BACKGROUND = (12, 15, 22)
GOLD = (230, 181, 74)
PACK_HEIGHT = 430  # altura da arte do pacote antes de reduzir para cada GIF

# Mesma palavra, mesma cor das séries das cartas (Incomum azul, Raro roxo…).
RARITY_COLORS = {
    "basico": (190, 198, 210),
    "incomum": (91, 156, 255),
    "raro": (176, 124, 255),
    "epico": (240, 150, 60),
    "lendario": (235, 75, 75),
}
RARITY_TITLES = {
    "basico": "BÁSICO",
    "incomum": "INCOMUM",
    "raro": "RARO",
    "epico": "ÉPICO",
    "lendario": "LENDÁRIO",
}
RARITY_POWER = {"basico": 0, "incomum": 1, "raro": 2, "epico": 3, "lendario": 4}
# Rótulos das fichas do lote ("1 RARA", "2 INCOMUNS"): falam de caixas, no feminino.
RARITY_SINGULARS = {
    "basico": "BÁSICA",
    "incomum": "INCOMUM",
    "raro": "RARA",
    "epico": "ÉPICA",
    "lendario": "LENDÁRIA",
}
RARITY_PLURALS = {
    "basico": "BÁSICAS",
    "incomum": "INCOMUNS",
    "raro": "RARAS",
    "epico": "ÉPICAS",
    "lendario": "LENDÁRIAS",
}


def _ease_out_cubic(t: float) -> float:
    return 1 - (1 - t) ** 3


def _ease_out_back(t: float) -> float:
    c1 = 1.2
    return 1 + (c1 + 1) * (t - 1) ** 3 + c1 * (t - 1) ** 2


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


@lru_cache(maxsize=2)
def _pack_parts() -> tuple[Image.Image, Image.Image]:
    body = artwork("pacote") or Image.new("RGBA", (340, 480), (30, 36, 48, 255))
    top = artwork("pacote_topo") or Image.new("RGBA", (340, 480), (0, 0, 0, 0))
    ratio = PACK_HEIGHT / body.height
    size = (int(body.width * ratio), PACK_HEIGHT)
    return body.resize(size, Image.LANCZOS), top.resize(size, Image.LANCZOS)


def _tracked(draw, center, text: str, size: int, fill, spacing: int):
    face = font(size, True)
    widths = [draw.textlength(letter, font=face) for letter in text]
    x = center[0] - (sum(widths) + spacing * (len(text) - 1)) / 2
    for letter, width in zip(text, widths):
        draw.text((x, center[1]), letter, font=face, fill=fill, anchor="lm")
        x += width + spacing


# --- Revelação com o prêmio na frente ------------------------------------------------------
#
# Regra de todos os GIFs do bot: o primeiro quadro já mostra o que importa (quem não
# carrega o GIF, ou tem a animação desligada, vê só ele). A animação vem por cima: luz,
# raios e partículas, sem esconder nada.

PRIZE_SIZE = (640, 360)
TILE = (168, 196)


@dataclass(frozen=True)
class PrizeTile:
    """Um cartão de prêmio: raridade (cor), ícone, título grande e linha de apoio."""

    rarity: str
    icon: str
    title: str
    subtitle: str = ""
    picture: Any = None  # caminho da imagem do cosmético, se houver


def _gif(frames: list[Image.Image], durations: list[int], loop: bool = True) -> bytes:
    """Paleta única tirada de vários quadros, para qualquer tamanho de quadro."""
    width, height = frames[0].size
    picks = [frames[0], frames[len(frames) // 3], frames[2 * len(frames) // 3], frames[-1]]
    strip = Image.new("RGB", (width, height * len(picks)))
    for index, frame in enumerate(picks):
        strip.paste(frame, (0, height * index))
    palette = strip.quantize(colors=255, method=Image.Quantize.MEDIANCUT)
    paletted = [frame.quantize(palette=palette, dither=Image.Dither.NONE) for frame in frames]
    buffer = BytesIO()
    paletted[0].save(
        buffer,
        format="GIF",
        save_all=True,
        append_images=paletted[1:],
        duration=durations,
        disposal=1,
        **({"loop": 0} if loop else {}),
    )
    return buffer.getvalue()


def _icon(kind: str, size: int, color, picture=None) -> Image.Image:
    """Ícone chapado e simples (o estilo minimalista do resto do bot)."""
    big = size * 2
    layer = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    c = big / 2
    r = big * 0.4
    light = mix(color, (255, 255, 255), 0.35)
    if kind == "cosmetic" and picture is not None:
        loaded = load_picture(picture)
        if loaded is not None:
            return contain(loaded, (size, size))
    if kind == "coin":
        draw.ellipse((c - r, c - r, c + r, c + r), fill=(*GOLD, 255))
        draw.ellipse(
            (c - r * 0.72, c - r * 0.72, c + r * 0.72, c + r * 0.72),
            outline=(120, 84, 20, 255),
            width=int(big * 0.03),
        )
        draw.text(
            (c, c), "M", font=font(int(big * 0.36), True), fill=(110, 76, 16, 255), anchor="mm"
        )
    elif kind == "rolls":
        s = r * 0.85
        draw.rounded_rectangle(
            (c - s, c - s, c + s, c + s), radius=int(big * 0.1), fill=(*light, 255)
        )
        pip = big * 0.06
        for dx, dy in ((-0.5, -0.5), (0.5, 0.5), (0, 0), (0.5, -0.5), (-0.5, 0.5)):
            x, y = c + dx * s, c + dy * s
            draw.ellipse((x - pip, y - pip, x + pip, y + pip), fill=(*BACKGROUND, 255))
    elif kind == "clock":
        draw.ellipse((c - r, c - r, c + r, c + r), outline=(*light, 255), width=int(big * 0.07))
        draw.line((c, c, c, c - r * 0.6), fill=(*light, 255), width=int(big * 0.06))
        draw.line((c, c, c + r * 0.45, c + r * 0.2), fill=(*light, 255), width=int(big * 0.06))
    elif kind == "shield":
        draw.polygon(
            [
                (c - r * 0.8, c - r * 0.75),
                (c + r * 0.8, c - r * 0.75),
                (c + r * 0.8, c),
                (c, c + r),
                (c - r * 0.8, c),
            ],
            fill=(*light, 255),
        )
    elif kind == "star":
        draw.polygon(star(c, c, r, r * 0.45), fill=(*light, 255))
    elif kind in {"plus", "up"}:
        w = big * 0.13
        if kind == "plus":
            draw.rectangle((c - w / 2, c - r, c + w / 2, c + r), fill=(*light, 255))
            draw.rectangle((c - r, c - w / 2, c + r, c + w / 2), fill=(*light, 255))
        else:
            draw.polygon([(c, c - r), (c + r * 0.8, c), (c - r * 0.8, c)], fill=(*light, 255))
            draw.rectangle((c - w / 2, c - 2, c + w / 2, c + r), fill=(*light, 255))
    elif kind == "tag":
        draw.polygon(
            [
                (c - r, c - r * 0.5),
                (c + r * 0.4, c - r * 0.5),
                (c + r, c),
                (c + r * 0.4, c + r * 0.5),
                (c - r, c + r * 0.5),
            ],
            fill=(*light, 255),
        )
        draw.ellipse(
            (c + r * 0.15, c - big * 0.04, c + r * 0.15 + big * 0.08, c + big * 0.04),
            fill=(*BACKGROUND, 255),
        )
    else:  # cosmético sem imagem: um losango
        draw.polygon([(c, c - r), (c + r, c), (c, c + r), (c - r, c)], fill=(*light, 255))
    return layer.resize((size, size), Image.LANCZOS)


def _tile(tile: PrizeTile) -> Image.Image:
    color = RARITY_COLORS[tile.rarity]
    width, height = TILE
    card = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(card)
    draw.rounded_rectangle((0, 0, width - 1, height - 1), 18, fill=(20, 25, 35, 245))
    draw.rounded_rectangle((0, 0, width - 1, height - 1), 18, outline=(*color, 255), width=3)
    icon = _icon(tile.icon, 78, color, tile.picture)
    card.alpha_composite(icon, ((width - icon.width) // 2, 20))
    face = font(24, True)
    title = tile.title
    while draw.textlength(title, font=face) > width - 18 and len(title) > 3:
        title = title[:-2].rstrip() + "…"
    draw.text((width / 2, 124), title, font=face, fill="white", anchor="mm")
    if tile.subtitle:
        small = font(15)
        subtitle = tile.subtitle
        while draw.textlength(subtitle, font=small) > width - 16 and len(subtitle) > 3:
            subtitle = subtitle[:-2].rstrip() + "…"
        draw.text((width / 2, 152), subtitle, font=small, fill=(176, 184, 198), anchor="mm")
    draw.text(
        (width / 2, 178),
        RARITY_TITLES[tile.rarity],
        font=font(12, True),
        fill=(*color, 255),
        anchor="mm",
    )
    return card


# Caixa e pacote têm abertura: o pacote treme, a aba voa e o conteúdo aparece. Depois
# vem o brilho e o GIF **para no último quadro** (sem loop), com tudo à vista, para a
# pessoa olhar com calma sem a animação reiniciar.
INTRO_FRAMES = 16
REVEAL_FRAMES = 12
SHIMMER_FRAMES = 24
FRAME_MS = 60
HOLD_MS = 60_000


def _mini_pack(height: int) -> tuple[Image.Image, Image.Image]:
    body, top = _pack_parts()
    ratio = height / body.height
    size = (max(1, int(body.width * ratio)), height)
    return body.resize(size, Image.LANCZOS), top.resize(size, Image.LANCZOS)


def _put(image, part, center_x, bottom, dx=0.0, dy=0.0, angle=0.0, alpha=1.0) -> None:
    piece = part.rotate(angle, resample=Image.BICUBIC, expand=True) if angle else part
    if alpha < 1:
        piece = piece.copy()
        piece.putalpha(piece.getchannel("A").point(lambda value: int(value * max(0.0, alpha))))
    x = int(center_x - piece.width / 2 + dx)
    y = int(bottom - part.height - (piece.height - part.height) / 2 + dy)
    image.alpha_composite(piece, (x, y))


def _opening(base: Image.Image, color, power: int, pack_height: int, bottom: int) -> list:
    """O pacote treme, a aba voa e a luz da raridade sai de dentro (quadros RGBA)."""
    width, height = base.size
    body, top = _mini_pack(pack_height)
    frames = []
    for index in range(INTRO_FRAMES):
        t = index / (INTRO_FRAMES - 1)
        image = base.copy()
        shake = t < 0.45
        intensity = (2 + 7 * t) if shake else 0
        dx = math.sin(index * 2.3) * intensity
        angle = math.sin(index * 1.7) * intensity * 0.5
        burst = _ease_out_cubic(_clamp((t - 0.45) / 0.55))
        image.alpha_composite(
            glow(
                (width, height),
                (width / 2, bottom - pack_height * 0.7),
                60 + 260 * burst,
                color if burst else GOLD,
                int(40 + (100 + 20 * power) * burst),
            )
        )
        _put(image, body, width / 2, bottom, dx=dx, angle=angle, alpha=1 - _clamp((t - 0.8) / 0.2))
        lid = burst
        _put(
            image,
            top,
            width / 2,
            bottom,
            dx=dx + 40 * lid,
            dy=-160 * lid,
            angle=angle - 25 * lid,
            alpha=1 - _clamp((t - 0.6) / 0.3),
        )
        frames.append(image)
    return frames


def _faded_layer(layer: Image.Image, alpha: float) -> Image.Image:
    if alpha >= 1:
        return layer
    faded = layer.copy()
    faded.putalpha(layer.getchannel("A").point(lambda value: int(value * max(0.0, alpha))))
    return faded


def _finish_reveal(frames: list[Image.Image], shimmer_ms: int = 70) -> bytes:
    """Abertura e revelação a 60 ms, brilho a 70 ms e o último quadro parado."""
    reveal = INTRO_FRAMES + REVEAL_FRAMES
    durations = [FRAME_MS] * reveal + [shimmer_ms] * (len(frames) - reveal)
    durations[-1] = HOLD_MS
    return _gif([frame.convert("RGB") for frame in frames], durations, loop=False)


def prize_gif(
    rarity: str,
    tiles: list[PrizeTile],
    *,
    headline: str | None = None,
    counts: dict[str, int] | None = None,
) -> bytes:
    """O pacote abre, os cartões dos prêmios surgem um a um e o GIF para com tudo à vista.

    Com `counts` (abertura em lote), o topo diz quantas caixas foram abertas e quantas
    saíram de cada raridade ("6 básicas · 2 incomuns · 1 rara"), acima dos cartões.
    """
    color = RARITY_COLORS[rarity]
    power = RARITY_POWER[rarity]
    width, height = PRIZE_SIZE
    shown = tiles[:3]
    base = Image.new("RGBA", PRIZE_SIZE, (*BACKGROUND, 255))
    base.alpha_composite(glow(PRIZE_SIZE, (width / 2, height * 0.62), 300, (40, 52, 76), 120))
    cards = [_tile(tile) for tile in shown]
    gap = 18
    row = sum(card.width for card in cards) + gap * (len(cards) - 1)
    x0 = (width - row) // 2
    top = 128
    content = Image.new("RGBA", PRIZE_SIZE, (0, 0, 0, 0))
    draw = ImageDraw.Draw(content)
    if counts:
        total = sum(counts.values())
        _tracked(draw, (width / 2, 40), f"{total} CAIXAS ABERTAS", 26, (255, 255, 255, 255), 5)
        draw.line((width / 2 - 46, 66, width / 2 + 46, 66), fill=(*color, 255), width=3)
        _rarity_pills(draw, counts, center_x=width / 2, y=96)
    else:
        _tracked(draw, (width / 2, 44), RARITY_TITLES[rarity], 34, (255, 255, 255, 255), 7)
        draw.line((width / 2 - 46, 72, width / 2 + 46, 72), fill=(*color, 255), width=3)
    if headline and not counts:
        draw.text((width / 2, 98), headline, font=font(17, True), fill=(176, 184, 198), anchor="mm")
    x = x0
    for card in cards:
        content.alpha_composite(card, (x, top))
        x += card.width + gap
    if len(tiles) > len(shown):
        draw.text(
            (width / 2, top + TILE[1] + 18),
            f"+ {len(tiles) - len(shown)} prêmio(s) na lista abaixo",
            font=font(14),
            fill=(176, 184, 198),
            anchor="mm",
        )
    generator = random.Random(power + 7)
    particles = [
        (
            generator.uniform(0, width),
            generator.uniform(0, height),
            generator.uniform(1.2, 3.0),
            generator.uniform(0.6, 1.6),
        )
        for _ in range(10 + power * 8)
    ]
    frames = _opening(base, color, power, 220, height - 40)
    title_layer = Image.new("RGBA", PRIZE_SIZE, (0, 0, 0, 0))
    title_layer.alpha_composite(content.crop((0, 0, width, top)), (0, 0))
    for index in range(REVEAL_FRAMES):
        t = index / (REVEAL_FRAMES - 1)
        image = base.copy()
        image.alpha_composite(
            glow(PRIZE_SIZE, (width / 2, top + TILE[1] / 2), 260, color, int(60 + 25 * power))
        )
        image.alpha_composite(_faded_layer(title_layer, _ease_out_cubic(t)))
        x = x0
        for number, card in enumerate(cards):
            local = _clamp((t - number * 0.18) / 0.55)
            if local > 0:
                scale = 0.7 + 0.3 * _ease_out_back(local)
                size = (max(1, int(card.width * scale)), max(1, int(card.height * scale)))
                small = _faded_layer(card.resize(size, Image.LANCZOS), local)
                image.alpha_composite(
                    small, (x + (card.width - size[0]) // 2, top + (card.height - size[1]) // 2)
                )
            x += card.width + gap
        frames.append(image)
    total = SHIMMER_FRAMES
    for index in range(total):
        t = index / total
        pulse = 0.5 + 0.5 * math.sin(t * 2 * math.pi)
        image = base.copy()
        image.alpha_composite(
            glow(
                PRIZE_SIZE,
                (width / 2, top + TILE[1] / 2),
                200 + 40 * pulse,
                color,
                int(50 + 25 * power + 30 * pulse),
            )
        )
        if power:
            rays = Image.new("RGBA", PRIZE_SIZE, (0, 0, 0, 0))
            ray_draw = ImageDraw.Draw(rays)
            cx, cy = width / 2, top + TILE[1] / 2
            count = 8 + power * 2
            for ray in range(count):
                angle = t * 2 * math.pi / count + ray * 2 * math.pi / count
                ray_draw.line(
                    (cx, cy, cx + 420 * math.cos(angle), cy + 420 * math.sin(angle)),
                    fill=(*mix(color, (255, 255, 255), 0.3), 20 + 8 * power),
                    width=3,
                )
            image.alpha_composite(rays.filter(ImageFilter.GaussianBlur(1.5)))
        dots = Image.new("RGBA", PRIZE_SIZE, (0, 0, 0, 0))
        dot_draw = ImageDraw.Draw(dots)
        for px, py, radius, speed in particles:
            y = (py - t * height * speed * 0.5) % height
            dot_draw.ellipse(
                (px - radius, y - radius, px + radius, y + radius),
                fill=(*mix(color, (255, 255, 255), 0.5), 150),
            )
        image.alpha_composite(dots)
        image.alpha_composite(content)
        frames.append(image)
    return _finish_reveal(frames)


def card_reveal_gif(card: Image.Image, color, headline: str) -> bytes:
    """O pacote abre, a carta sobe de dentro dele, um brilho passa e o GIF para na carta."""
    width, height = 560, 500
    card = card.convert("RGBA")
    card.thumbnail((300, 420), Image.LANCZOS)
    x, y = (width - card.width) // 2, 64
    base = Image.new("RGBA", (width, height), (*BACKGROUND, 255))
    title = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    _tracked(ImageDraw.Draw(title), (width / 2, 32), headline, 22, (255, 255, 255, 255), 5)
    mask = card.getchannel("A")
    frames = _opening(base, color, 3, 260, height - 30)
    for index in range(REVEAL_FRAMES):
        t = _ease_out_cubic(index / (REVEAL_FRAMES - 1))
        image = base.copy()
        image.alpha_composite(
            glow((width, height), (width / 2, y + card.height / 2), 250, color, 110)
        )
        image.alpha_composite(_faded_layer(card, min(1.0, t * 1.5)), (x, int(y + 160 * (1 - t))))
        image.alpha_composite(_faded_layer(title, t))
        frames.append(image)
    total = SHIMMER_FRAMES + 2
    for index in range(total):
        t = index / total
        pulse = 0.5 + 0.5 * math.sin(t * 2 * math.pi)
        image = base.copy()
        image.alpha_composite(
            glow(
                (width, height),
                (width / 2, y + card.height / 2),
                230 + 30 * pulse,
                color,
                int(90 + 40 * pulse),
            )
        )
        image.alpha_composite(card, (x, y))
        # Faixa de luz diagonal atravessando a carta (só por cima, não esconde nada).
        sweep = Image.new("L", card.size, 0)
        position = -card.width + (card.width * 3) * t
        ImageDraw.Draw(sweep).polygon(
            [
                (position, 0),
                (position + 50, 0),
                (position - 60, card.height),
                (position - 110, card.height),
            ],
            fill=70,
        )
        sweep = ImageChops.multiply(sweep.filter(ImageFilter.GaussianBlur(8)), mask)
        shine = Image.new("RGBA", card.size, (255, 255, 255, 0))
        shine.putalpha(sweep)
        image.alpha_composite(shine, (x, y))
        image.alpha_composite(title)
        frames.append(image)
    return _finish_reveal(frames)


def _rarity_pills(draw, counts: dict[str, int], *, center_x: float, y: float) -> None:
    """Fichas "6 BÁSICAS · 2 INCOMUNS · 1 RARA" centradas, cada uma na cor da raridade."""
    face = font(15, True)
    pills = []
    for rarity in RARITY_COLORS:
        amount = counts.get(rarity, 0)
        if amount:
            label = RARITY_SINGULARS[rarity] if amount == 1 else RARITY_PLURALS[rarity]
            text = f"{amount} {label}"
            pills.append((text, RARITY_COLORS[rarity], draw.textlength(text, font=face) + 26))
    gap = 8
    x = center_x - (sum(width for *_, width in pills) + gap * (len(pills) - 1)) / 2
    for text, color, width in pills:
        draw.rounded_rectangle((x, y - 14, x + width, y + 14), 14, fill=(20, 25, 35, 235))
        draw.rounded_rectangle((x, y - 14, x + width, y + 14), 14, outline=(*color, 255), width=2)
        draw.text((x + width / 2, y + 1), text, font=face, fill=(*color, 255), anchor="mm")
        x += width + gap
