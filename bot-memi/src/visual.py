"""Peças de desenho compartilhadas pelas imagens do FUT.AE (Pillow).

Mesma linguagem da carta e da escalação: fundo azul-noite, painéis escuros com cantos
arredondados, texto branco em Manrope e a cor do time ou da raridade como destaque.
"""

from __future__ import annotations

from functools import lru_cache

import math
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageOps

from src.crests import team_logo_path
from src.lineup_image import _fit, _font, _rgb
from src.roll_images import find_image

NIGHT = (16, 20, 28)
PANEL = (10, 14, 20)
MUTED = (165, 175, 190)
LINE = (226, 242, 230)
WIN, DRAW, LOSS = (46, 160, 90), (120, 128, 140), (205, 60, 60)

font = _font
fit = _fit
rgb = _rgb


def mix(a: tuple[int, ...], b: tuple[int, ...], amount: float) -> tuple[int, ...]:
    """Mistura duas cores (0 = a, 1 = b)."""
    return tuple(int(round(x + (y - x) * amount)) for x, y in zip(a, b))


def shade(color: tuple[int, int, int], factor: float) -> tuple[int, int, int]:
    return tuple(max(0, min(255, int(channel * factor))) for channel in color)


def vertical_gradient(size: tuple[int, int], top, bottom) -> Image.Image:
    width, height = size
    column = Image.new("RGBA", (1, height))
    for y in range(height):
        column.putpixel((0, y), (*mix(top, bottom, y / max(1, height - 1)), 255))
    return column.resize(size)


def horizontal_gradient(size: tuple[int, int], left, right) -> Image.Image:
    width, height = size
    row = Image.new("RGBA", (width, 1))
    for x in range(width):
        row.putpixel((x, 0), (*mix(left, right, x / max(1, width - 1)), 255))
    return row.resize(size)


def glow(size: tuple[int, int], center, radius: float, color, strength: int = 160) -> Image.Image:
    """Camada com um brilho radial suave (para compor por cima)."""
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    cx, cy = center
    draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), fill=(*color[:3], strength))
    return layer.filter(ImageFilter.GaussianBlur(radius * 0.45))


def rounded_mask(size: tuple[int, int], radius: int) -> Image.Image:
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), radius, fill=255)
    return mask


def panel(image: Image.Image, box, radius: int = 22, fill=(10, 14, 20, 210), outline=None):
    layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
    ImageDraw.Draw(layer).rounded_rectangle(
        box, radius, fill=fill, outline=outline, width=3 if outline else 0
    )
    image.alpha_composite(layer)


def load_picture(path: Any, entity: str | None = None, entity_id: Any = None) -> Image.Image | None:
    found = find_image(path, entity, entity_id)
    if found is None:
        return None
    try:
        with Image.open(found) as opened:
            return opened.convert("RGBA")
    except OSError:
        return None


def contain(picture: Image.Image, size: tuple[int, int]) -> Image.Image:
    """Redimensiona sem cortar e centraliza numa tela transparente do tamanho pedido."""
    fitted = picture.copy()
    fitted.thumbnail(size, Image.LANCZOS)
    canvas = Image.new("RGBA", size, (0, 0, 0, 0))
    canvas.alpha_composite(fitted, ((size[0] - fitted.width) // 2, (size[1] - fitted.height) // 2))
    return canvas


def shadowed(picture: Image.Image, blur: int = 10, opacity: int = 150) -> Image.Image:
    """A figura com uma sombra macia embaixo, para destacar do fundo."""
    pad = blur * 3
    base = Image.new("RGBA", (picture.width + pad * 2, picture.height + pad * 2), (0, 0, 0, 0))
    alpha = picture.getchannel("A").point(lambda value: value * opacity // 255)
    shadow = Image.new("RGBA", picture.size, (0, 0, 0, 255))
    shadow.putalpha(alpha)
    base.alpha_composite(shadow, (pad, pad + blur // 2))
    base = base.filter(ImageFilter.GaussianBlur(blur))
    base.alpha_composite(picture, (pad, pad))
    return base


def initials(name: str) -> str:
    words = [word for word in str(name or "?").replace("-", " ").split() if word[:1].isalnum()]
    ignored = {"fc", "ec", "sc", "de", "da", "do", "das", "dos"}
    core = [word for word in words if word.casefold() not in ignored] or words or ["?"]
    return "".join(word[0] for word in core[:3]).upper()


def monogram(name: str, color, size: int) -> Image.Image:
    """Escudo chapado com as iniciais, para times sem emblema equipado."""
    big = size * 3
    image = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    points = [
        (big * 0.5, big * 0.06),
        (big * 0.9, big * 0.18),
        (big * 0.84, big * 0.62),
        (big * 0.5, big * 0.94),
        (big * 0.16, big * 0.62),
        (big * 0.1, big * 0.18),
    ]
    draw.polygon(points, fill=(*color, 255))
    inner = [
        (big * 0.5 + (x - big * 0.5) * 0.88, big * 0.5 + (y - big * 0.5) * 0.88) for x, y in points
    ]
    draw.line(inner + inner[:1], fill=(255, 255, 255, 110), width=max(2, big // 120))
    text = initials(name)
    size_font = int(big * (0.34 if len(text) < 3 else 0.26))
    draw.text((big * 0.5, big * 0.47), text, font=font(size_font, True), fill="white", anchor="mm")
    return image.resize((size, size), Image.LANCZOS)


def emblem(team: dict[str, Any], size: int) -> Image.Image:
    """Escudo custom ou emblema equipado do time, ou um monograma na cor do time."""
    picture = load_picture(team_logo_path(team))
    if picture is not None:
        return contain(picture, (size, size))
    return monogram(team.get("team_name") or "?", rgb(team.get("color") or "#2E8B57"), size)


def text_center(draw, xy, text, size, bold=True, fill="white", **kwargs):
    draw.text(xy, text, font=font(size, bold), fill=fill, anchor="mm", **kwargs)


def pill(draw, center, text, size, fill, text_fill=(15, 15, 20), padding=(18, 8)):
    """Etiqueta arredondada centralizada em `center`."""
    face = font(size, True)
    width = draw.textlength(text, font=face)
    x, y = center
    half_w, half_h = width / 2 + padding[0], size / 2 + padding[1]
    draw.rounded_rectangle((x - half_w, y - half_h, x + half_w, y + half_h), half_h, fill=fill)
    draw.text((x, y), text, font=face, fill=text_fill, anchor="mm")
    return half_w * 2


@lru_cache(maxsize=16)
def noise(size: tuple[int, int], amount: int = 10, seed: int = 7) -> Image.Image:
    """Granulado sutil (deixa os gradientes menos chapados).

    É sempre o mesmo para o mesmo tamanho, então fica em cache (gerar custava ~0,3 s).
    Quem usa não altera a imagem devolvida.
    """
    import random

    generator = random.Random(seed)
    small = Image.new("L", (size[0] // 2, size[1] // 2))
    small.putdata(
        [128 + generator.randint(-amount, amount) for _ in range(small.width * small.height)]
    )
    return small.resize(size)


def apply_grain(image: Image.Image, amount: int = 8) -> Image.Image:
    grain = noise(image.size, amount).convert("RGBA")
    blended = ImageChops.overlay(image.convert("RGBA"), grain)
    return Image.blend(image.convert("RGBA"), blended, 0.5)


def vignette(image: Image.Image, strength: int = 140) -> None:
    width, height = image.size
    mask = Image.new("L", (width, height), 0)
    ImageDraw.Draw(mask).ellipse(
        (-width * 0.25, -height * 0.35, width * 1.25, height * 1.35), fill=255
    )
    mask = ImageOps.invert(mask.filter(ImageFilter.GaussianBlur(min(width, height) * 0.18)))
    dark = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    dark.putalpha(mask.point(lambda value: value * strength // 255))
    image.alpha_composite(dark)


def star(cx: float, cy: float, outer: float, inner: float, rotation: float = 0.0):
    points = []
    for index in range(10):
        radius = outer if index % 2 == 0 else inner
        angle = math.radians(-90 + index * 36) + rotation
        points.append((cx + radius * math.cos(angle), cy + radius * math.sin(angle)))
    return points


def to_png(image: Image.Image) -> BytesIO:
    buffer = BytesIO()
    image.convert("RGB").save(buffer, format="PNG", compress_level=6)
    buffer.seek(0)
    return buffer


def save_preview(buffer: BytesIO, path: str | Path) -> None:
    Path(path).write_bytes(buffer.getvalue())
    buffer.seek(0)
