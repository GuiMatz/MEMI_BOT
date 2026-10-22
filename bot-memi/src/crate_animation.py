"""Animações da crate em GIF: o pacote tremendo e a revelação de cada raridade.

A abertura acontece em dois tempos no Discord: primeiro o pacote treme (GIF neutro, que
não entrega a raridade, em loop); depois a mensagem é editada com a revelação daquela
raridade, que toca uma vez e para no último quadro (GIF sem extensão de loop).

O pacote é a arte `assets/ilustracoes/pacote.png` (corpo) e `pacote_topo.png` (aba);
a animação é montada aqui com Pillow e guardada em memória.
"""

from __future__ import annotations

import math
import random
from functools import lru_cache
from io import BytesIO

from PIL import Image, ImageDraw, ImageFilter

from src.art import artwork
from src.visual import font, glow, mix

WIDTH, HEIGHT = 480, 300
SCALE = 2  # desenha em dobro e reduz: bordas suaves
BIG = (WIDTH * SCALE, HEIGHT * SCALE)
BACKGROUND = (12, 15, 22)
GOLD = (230, 181, 74)
PACK_HEIGHT = 430  # altura do pacote no quadro grande
PACK_BASE = 560  # onde fica o pé do pacote

RARITY_COLORS = {
    "basico": (190, 198, 210),
    "incomum": (76, 207, 134),
    "raro": (91, 156, 255),
    "epico": (176, 124, 255),
    "lendario": (255, 179, 71),
}
RARITY_TITLES = {
    "basico": "BÁSICO",
    "incomum": "INCOMUM",
    "raro": "RARO",
    "epico": "ÉPICO",
    "lendario": "LENDÁRIO",
}
RARITY_POWER = {"basico": 0, "incomum": 1, "raro": 2, "epico": 3, "lendario": 4}


def _ease_out_cubic(t: float) -> float:
    return 1 - (1 - t) ** 3


def _ease_out_back(t: float) -> float:
    c1 = 1.2
    return 1 + (c1 + 1) * (t - 1) ** 3 + c1 * (t - 1) ** 2


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


@lru_cache(maxsize=1)
def _background() -> Image.Image:
    image = Image.new("RGBA", BIG, (*BACKGROUND, 255))
    image.alpha_composite(glow(BIG, (BIG[0] / 2, BIG[1] * 0.55), 380, (40, 52, 76), 120))
    return image


@lru_cache(maxsize=2)
def _pack_parts() -> tuple[Image.Image, Image.Image]:
    body = artwork("pacote") or Image.new("RGBA", (340, 480), (30, 36, 48, 255))
    top = artwork("pacote_topo") or Image.new("RGBA", (340, 480), (0, 0, 0, 0))
    ratio = PACK_HEIGHT / body.height
    size = (int(body.width * ratio), PACK_HEIGHT)
    return body.resize(size, Image.LANCZOS), top.resize(size, Image.LANCZOS)


def _place(image: Image.Image, part: Image.Image, dx=0.0, dy=0.0, angle=0.0, alpha=1.0) -> None:
    piece = part.rotate(angle, resample=Image.BICUBIC, expand=True) if angle else part
    if alpha < 1:
        piece = piece.copy()
        piece.putalpha(piece.getchannel("A").point(lambda value: int(value * alpha)))
    x = int(BIG[0] / 2 - piece.width / 2 + dx)
    y = int(PACK_BASE - part.height - (piece.height - part.height) / 2 + dy)
    image.alpha_composite(piece, (x, y))


def _to_gif(frames: list[Image.Image], durations: list[int], loop: bool) -> bytes:
    # Paleta única tirada de vários quadros: sem faixas nem piscadas.
    picks = [frames[0], frames[len(frames) // 3], frames[2 * len(frames) // 3], frames[-1]]
    strip = Image.new("RGB", (WIDTH, HEIGHT * len(picks)))
    for index, frame in enumerate(picks):
        strip.paste(frame, (0, HEIGHT * index))
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


def _finish(image: Image.Image) -> Image.Image:
    return image.resize((WIDTH, HEIGHT), Image.LANCZOS).convert("RGB")


@lru_cache(maxsize=1)
def opening_gif() -> bytes:
    """O pacote tremendo cada vez mais forte, em loop (não revela a raridade)."""
    body, top = _pack_parts()
    frames, durations = [], []
    total = 20
    for index in range(total):
        t = index / total
        image = _background().copy()
        image.alpha_composite(
            glow(BIG, (BIG[0] / 2, PACK_BASE - PACK_HEIGHT / 2), 200, GOLD, int(30 + 50 * t))
        )
        intensity = 2 + 9 * t
        dx = math.sin(index * 2.1) * intensity
        angle = math.sin(index * 1.7) * (1 + 3.5 * t)
        _place(image, body, dx=dx, angle=angle)
        _place(image, top, dx=dx, dy=-abs(math.sin(index * 1.3)) * 6 * t, angle=angle)
        frames.append(_finish(image))
        durations.append(60)
    return _to_gif(frames, durations, loop=True)


def _rays(color, count: int, rotation: float, length: float, alpha: int) -> Image.Image:
    layer = Image.new("RGBA", BIG, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    cx, cy = BIG[0] / 2, PACK_BASE - PACK_HEIGHT * 0.72
    for index in range(count):
        angle = rotation + index * 2 * math.pi / count
        draw.line(
            (cx, cy, cx + length * math.cos(angle), cy + length * math.sin(angle)),
            fill=(*color, alpha),
            width=3 if index % 2 else 5,
        )
    return layer.filter(ImageFilter.GaussianBlur(1.5))


def _tracked(draw, center, text: str, size: int, fill, spacing: int):
    face = font(size, True)
    widths = [draw.textlength(letter, font=face) for letter in text]
    x = center[0] - (sum(widths) + spacing * (len(text) - 1)) / 2
    for letter, width in zip(text, widths):
        draw.text((x, center[1]), letter, font=face, fill=fill, anchor="lm")
        x += width + spacing


@lru_cache(maxsize=8)
def reveal_gif(rarity: str) -> bytes:
    """A aba se solta, a luz da raridade sai do pacote e o nome da série aparece."""
    color = RARITY_COLORS[rarity]
    power = RARITY_POWER[rarity]
    body, top = _pack_parts()
    total = 24 + power * 3
    generator = random.Random(power + 11)
    particles = [
        (
            generator.uniform(-math.pi * 0.95, -math.pi * 0.05),
            generator.uniform(120, 380),
            generator.uniform(2, 4.5),
        )
        for _ in range(8 + power * 6)
    ]
    mouth = (BIG[0] / 2, PACK_BASE - PACK_HEIGHT * 0.72)
    frames, durations = [], []
    for index in range(total):
        t = index / (total - 1)
        image = _background().copy()
        burst = _ease_out_cubic(_clamp(t / 0.4))
        image.alpha_composite(glow(BIG, mouth, 90 + 220 * burst, color, int(60 + 90 * burst)))
        if power:
            image.alpha_composite(
                _rays(
                    mix(color, (255, 255, 255), 0.3),
                    10 + power * 2,
                    t * 0.5,
                    120 + 300 * burst,
                    40 + 14 * power,
                )
            )
        _place(image, body)
        lid = _ease_out_cubic(_clamp(t / 0.45))
        _place(
            image,
            top,
            dx=60 * lid,
            dy=-240 * lid,
            angle=-28 * lid,
            alpha=1 - _clamp((t - 0.2) / 0.25),
        )
        layer = Image.new("RGBA", BIG, (0, 0, 0, 0))
        overlay = ImageDraw.Draw(layer)
        line = 150 * burst
        overlay.line(
            (mouth[0] - line, mouth[1], mouth[0] + line, mouth[1]),
            fill=(*mix(color, (255, 255, 255), 0.6), int(255 * burst)),
            width=4,
        )
        for angle, distance, radius in particles:
            travel = _ease_out_cubic(_clamp((t - 0.08) / 0.6)) * distance
            fade = 1 - _clamp((t - 0.5) / 0.45)
            if travel <= 0 or fade <= 0:
                continue
            px = mouth[0] + math.cos(angle) * travel
            py = mouth[1] + math.sin(angle) * travel * 0.8
            overlay.ellipse(
                (px - radius, py - radius, px + radius, py + radius),
                fill=(*mix(color, (255, 255, 255), 0.5), int(230 * fade)),
            )
        image.alpha_composite(layer)
        label_t = _clamp((t - 0.3) / 0.4)
        if label_t > 0:
            text_layer = Image.new("RGBA", BIG, (0, 0, 0, 0))
            size = int(44 * SCALE * (0.8 + 0.2 * _ease_out_back(label_t)))
            _tracked(
                ImageDraw.Draw(text_layer),
                (BIG[0] / 2, 96),
                RARITY_TITLES[rarity],
                size,
                (255, 255, 255, 255),
                size // 5,
            )
            text_layer.putalpha(
                text_layer.getchannel("A").point(lambda value: int(value * min(1, label_t * 1.6)))
            )
            image.alpha_composite(text_layer)
            bar = 70 * _ease_out_cubic(label_t)
            ImageDraw.Draw(image).line(
                (BIG[0] / 2 - bar, 150, BIG[0] / 2 + bar, 150), fill=(*color, 255), width=4
            )
        frames.append(_finish(image))
        durations.append(55)
    durations[-1] = 60_000  # para no último quadro
    return _to_gif(frames, durations, loop=False)
