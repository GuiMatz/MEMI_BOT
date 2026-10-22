"""Gera a moeda Memilets (gato de óculos).

Os ícones dos upgrades são SVG (`scripts/renderizar_ilustracoes.py`).

Os PNGs vão para `assets/emojis/futae/` na raiz do repositório; o MeMi BOT os envia
como emojis da aplicação ao ligar (ver `emojis.py`). Rode de novo depois de mudar cores
ou desenhos:

    python -m scripts.gerar_icones
"""

from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw

from src.icons import ICON_DIR

SIZE = 128
SCALE = 4  # desenha 4x maior e reduz, para bordas suaves
WHITE = (255, 255, 255, 255)


def _canvas() -> tuple[Image.Image, ImageDraw.ImageDraw, int]:
    big = SIZE * SCALE
    image = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    return image, ImageDraw.Draw(image), big


def _finish(image: Image.Image, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    image.resize((SIZE, SIZE), Image.LANCZOS).save(path, optimize=True)


def _rgb(hex_color: str) -> tuple[int, int, int]:
    value = hex_color.lstrip("#")
    return tuple(int(value[index : index + 2], 16) for index in (0, 2, 4))


def _shade(color: tuple[int, int, int], factor: float) -> tuple[int, int, int, int]:
    return (*(max(0, min(255, int(channel * factor))) for channel in color), 255)


def _disc(image: Image.Image, big: int, color: tuple[int, int, int]) -> None:
    """Disco com borda mais escura e um brilho suave em cima, o fundo de todo ícone."""
    draw = ImageDraw.Draw(image)
    pad = big * 0.04
    draw.ellipse((pad, pad, big - pad, big - pad), fill=_shade(color, 0.7))
    inner = big * 0.09
    draw.ellipse((inner, inner, big - inner, big - inner), fill=_shade(color, 1.0))
    glow = Image.new("RGBA", image.size, (0, 0, 0, 0))
    ImageDraw.Draw(glow).ellipse(
        (big * 0.2, big * 0.12, big * 0.8, big * 0.42), fill=(255, 255, 255, 45)
    )
    image.alpha_composite(glow)


def _star(cx: float, cy: float, outer: float, inner: float) -> list[tuple[float, float]]:
    points = []
    for index in range(10):
        radius = outer if index % 2 == 0 else inner
        angle = math.radians(-90 + index * 36)
        points.append((cx + radius * math.cos(angle), cy + radius * math.sin(angle)))
    return points


def memilets_coin(path: Path) -> None:
    """Moeda dourada com um gato de óculos estampado."""
    image, draw, big = _canvas()
    gold, dark = (242, 184, 48), (176, 112, 20)
    pad = big * 0.03
    draw.ellipse((pad, pad, big - pad, big - pad), fill=(*dark, 255))
    rim = big * 0.08
    draw.ellipse((rim, rim, big - rim, big - rim), fill=(*gold, 255))
    edge = big * 0.13
    draw.ellipse((edge, edge, big - edge, big - edge), outline=(*dark, 255), width=int(big * 0.02))
    c = big / 2
    ink = (70, 44, 10, 255)
    # Cabeça e orelhas do gato.
    head = big * 0.25
    draw.polygon(
        [
            (c - head * 0.95, c - head * 0.25),
            (c - head * 0.75, c - head * 1.25),
            (c - head * 0.2, c - head * 0.75),
        ],
        fill=ink,
    )
    draw.polygon(
        [
            (c + head * 0.95, c - head * 0.25),
            (c + head * 0.75, c - head * 1.25),
            (c + head * 0.2, c - head * 0.75),
        ],
        fill=ink,
    )
    draw.ellipse((c - head, c - head * 0.85, c + head, c + head * 0.95), fill=ink)
    # Óculos: duas lentes claras e a ponte.
    lens = head * 0.38
    for side in (-1, 1):
        lx = c + side * head * 0.45
        ly = c - head * 0.05
        draw.ellipse(
            (lx - lens, ly - lens, lx + lens, ly + lens),
            fill=(255, 236, 170, 255),
            outline=(255, 255, 255, 255),
            width=int(big * 0.018),
        )
        draw.ellipse((lx - lens * 0.3, ly - lens * 0.3, lx + lens * 0.3, ly + lens * 0.3), fill=ink)
    draw.line(
        (c - head * 0.07, c - head * 0.1, c + head * 0.07, c - head * 0.1),
        fill=(255, 255, 255, 255),
        width=int(big * 0.022),
    )
    # Nariz e bigodes.
    nose = head * 0.12
    draw.polygon(
        [(c - nose, c + head * 0.42), (c + nose, c + head * 0.42), (c, c + head * 0.55)],
        fill=(240, 140, 150, 255),
    )
    for side in (-1, 1):
        for tilt in (-0.12, 0.08):
            draw.line(
                (
                    c + side * head * 0.35,
                    c + head * 0.55,
                    c + side * head * 1.15,
                    c + head * (0.5 + tilt),
                ),
                fill=(255, 236, 170, 255),
                width=int(big * 0.012),
            )
    _finish(image, path)


def main() -> None:
    memilets_coin(ICON_DIR / "memilets.png")
    print(f"Ícones gerados em {ICON_DIR}")


if __name__ == "__main__":
    main()
