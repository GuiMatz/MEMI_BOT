"""Gera a moeda Memilets (gato de óculos) e os ícones redondos dos upgrades.

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
from src.perks import UPGRADES

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


def _binoculars(draw, big):
    c = big / 2
    r = big * 0.13
    for side in (-1, 1):
        x = c + side * big * 0.13
        draw.rounded_rectangle((x - r * 0.75, c - big * 0.2, x + r * 0.75, c), r * 0.3, fill=WHITE)
        draw.ellipse((x - r, c - r * 0.2, x + r, c + r * 1.8), fill=WHITE)
    draw.rectangle((c - big * 0.06, c - big * 0.08, c + big * 0.06, c + big * 0.03), fill=WHITE)


def _eye(draw, big):
    c = big / 2
    w, h = big * 0.3, big * 0.17
    almond = [
        (c + w * math.cos(math.radians(angle)), c - h * math.sin(math.radians(angle)) ** 1)
        for angle in range(0, 181, 10)
    ] + [
        (c + w * math.cos(math.radians(angle)), c + h * math.sin(math.radians(angle)))
        for angle in range(180, -1, -10)
    ]
    draw.polygon(almond, fill=WHITE)
    pupil = h * 0.85
    draw.ellipse((c - pupil, c - pupil, c + pupil, c + pupil), fill=(30, 30, 30, 255))
    draw.ellipse((c - pupil * 0.45, c - pupil * 0.6, c - pupil * 0.05, c - pupil * 0.2), fill=WHITE)


def _star_icon(draw, big):
    draw.polygon(_star(big / 2, big * 0.52, big * 0.27, big * 0.11), fill=WHITE)


def _clock(draw, big):
    c = big / 2
    r = big * 0.25
    draw.ellipse((c - r, c - r, c + r, c + r), outline=WHITE, width=int(big * 0.05))
    draw.line((c, c, c, c - r * 0.65), fill=WHITE, width=int(big * 0.045))
    draw.line((c, c, c + r * 0.5, c + r * 0.25), fill=WHITE, width=int(big * 0.045))


def _shield(draw, big):
    c = big / 2
    draw.polygon(
        [
            (c, big * 0.22),
            (c + big * 0.22, big * 0.3),
            (c + big * 0.19, big * 0.56),
            (c, big * 0.78),
            (c - big * 0.19, big * 0.56),
            (c - big * 0.22, big * 0.3),
        ],
        fill=WHITE,
    )


def _coins(draw, big):
    c = big / 2
    w, h = big * 0.2, big * 0.06
    for index in range(4):
        y = big * 0.66 - index * h * 1.45
        draw.ellipse((c - w, y - h, c + w, y + h), fill=WHITE, outline=(0, 0, 0, 60), width=4)


def _lock(draw, big):
    c = big / 2
    stroke = int(big * 0.065)
    draw.rounded_rectangle(
        (c - big * 0.15, big * 0.2, c + big * 0.15, big * 0.56),
        big * 0.15,
        outline=WHITE,
        width=stroke,
    )
    draw.rounded_rectangle(
        (c - big * 0.23, big * 0.42, c + big * 0.23, big * 0.76), big * 0.05, fill=WHITE
    )
    draw.ellipse((c - big * 0.04, big * 0.53, c + big * 0.04, big * 0.61), fill=(0, 0, 0, 0))


def _hourglass(draw, big):
    c = big / 2
    top, bottom, w = big * 0.22, big * 0.78, big * 0.18
    draw.rectangle((c - w * 1.2, top - big * 0.03, c + w * 1.2, top + big * 0.02), fill=WHITE)
    draw.rectangle((c - w * 1.2, bottom - big * 0.02, c + w * 1.2, bottom + big * 0.03), fill=WHITE)
    draw.polygon([(c - w, top), (c + w, top), (c, c)], fill=WHITE)
    draw.polygon([(c - w, bottom), (c + w, bottom), (c, c)], fill=WHITE)


def _contract(draw, big):
    """Folha de contrato com uma marca de confirmado."""
    c = big / 2
    draw.rounded_rectangle(
        (c - big * 0.19, big * 0.2, c + big * 0.19, big * 0.78), big * 0.03, fill=WHITE
    )
    for index in range(3):
        y = big * (0.31 + index * 0.09)
        draw.rectangle((c - big * 0.12, y, c + big * 0.12, y + big * 0.03), fill=(0, 0, 0, 0))
    draw.line(
        (c - big * 0.08, big * 0.64, c - big * 0.02, big * 0.7, c + big * 0.11, big * 0.57),
        fill=(0, 0, 0, 0),
        width=int(big * 0.04),
    )


def _ticket(draw, big):
    c = big / 2
    box = (c - big * 0.28, c - big * 0.15, c + big * 0.28, c + big * 0.15)
    draw.rounded_rectangle(box, big * 0.03, fill=WHITE)
    for side in (-1, 1):
        x = c + side * big * 0.28
        draw.ellipse(
            (x - big * 0.06, c - big * 0.06, x + big * 0.06, c + big * 0.06), fill=(0, 0, 0, 0)
        )
    for y in range(int(c - big * 0.12), int(c + big * 0.12), int(big * 0.05)):
        draw.rectangle((c + big * 0.08, y, c + big * 0.1, y + big * 0.025), fill=(0, 0, 0, 0))


def _chart(draw, big):
    base = big * 0.72
    for index, height in enumerate((0.16, 0.26, 0.38)):
        x = big * (0.28 + index * 0.16)
        draw.rounded_rectangle(
            (x, base - big * height, x + big * 0.11, base), big * 0.02, fill=WHITE
        )


PICTOGRAMS = {
    "rolls": _binoculars,
    "wishes": _eye,
    "star_wishes": _star_icon,
    "daily": _clock,
    "wish_protection": _shield,
    "daily_max": _coins,
    "exclusivity": _lock,
    "dismiss_refund": _contract,
    "box_office_cap": _ticket,
    "match_income": _chart,
}


def upgrade_icon(key: str, path: Path) -> None:
    image, _, big = _canvas()
    _disc(image, big, _rgb(UPGRADES[key].color))
    # Os furos do ingresso usam transparência: desenha o pictograma numa camada própria.
    layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
    PICTOGRAMS[key](ImageDraw.Draw(layer), big)
    image.alpha_composite(layer)
    _finish(image, path)


def main() -> None:
    memilets_coin(ICON_DIR / "memilets.png")
    for key in UPGRADES:
        upgrade_icon(key, ICON_DIR / f"upg_{key}.png")
    print(f"Ícones gerados em {ICON_DIR}")


if __name__ == "__main__":
    main()
