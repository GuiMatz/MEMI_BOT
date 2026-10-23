"""Carta de cosmético para a galeria do `mm!cosmeticos` (uma por página, estilo Mudae).

Mesma linha minimalista das cartas: fundo chapado, borda fina na cor do tipo. Itens com
imagem própria (emblema, uniforme, alguns mascotes) mostram a imagem; os outros usam o
medalhão do tipo (`assets/ilustracoes/<tipo>.png`).
"""

from __future__ import annotations

from io import BytesIO
from typing import Any

from PIL import Image, ImageDraw

from src.art import artwork
from src.visual import (
    MUTED,
    contain,
    fit,
    font,
    glow,
    load_picture,
    mix,
    rounded_mask,
    text_center,
    to_png,
)

WIDTH, HEIGHT = 600, 780
ART = (24, 24, WIDTH - 24, 560)
BACKGROUND = (14, 18, 25)

# Cor de cada tipo de cosmético (borda e destaques).
KIND_COLORS = {
    "emblema": (226, 92, 92),
    "uniforme": (76, 143, 240),
    "estadio": (60, 191, 122),
    "tecnico": (156, 124, 240),
    "liga": (232, 184, 74),
    "mascote": (240, 138, 66),
}
KIND_LABELS = {
    "emblema": "EMBLEMA",
    "uniforme": "UNIFORME",
    "estadio": "ESTÁDIO",
    "tecnico": "TÉCNICO",
    "liga": "LIGA",
    "mascote": "MASCOTE",
}


def _tracked(draw: ImageDraw.ImageDraw, center, text: str, size: int, fill, spacing: int = 6):
    """Texto em caixa alta com espaçamento entre letras, centralizado."""
    face = font(size, True)
    widths = [draw.textlength(letter, font=face) for letter in text]
    total = sum(widths) + spacing * (len(text) - 1)
    x = center[0] - total / 2
    for letter, width in zip(text, widths):
        draw.text((x, center[1]), letter, font=face, fill=fill, anchor="lm")
        x += width + spacing


def render_cosmetic_card(
    item: dict[str, Any], *, equipped: bool = False, owner: str | None = None
) -> BytesIO:
    kind = item["kind"]
    color = KIND_COLORS[kind]
    image = Image.new("RGBA", (WIDTH, HEIGHT), (*BACKGROUND, 255))
    art_w, art_h = ART[2] - ART[0], ART[3] - ART[1]
    art = Image.new("RGBA", (art_w, art_h), (*mix(BACKGROUND, color, 0.08), 255))
    art.alpha_composite(glow((art_w, art_h), (art_w / 2, art_h / 2), 210, color, 70))
    picture = load_picture(item.get("image_path"))
    if picture is not None:
        figure = contain(picture, (art_w - 140, art_h - 140))
    else:
        figure = contain(artwork(kind) or Image.new("RGBA", (1, 1)), (340, 340))
    art.alpha_composite(figure, ((art_w - figure.width) // 2, (art_h - figure.height) // 2))
    image.paste(art, ART[:2], rounded_mask((art_w, art_h), 18))

    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((3, 3, WIDTH - 4, HEIGHT - 4), 26, outline=color, width=5)
    draw.rounded_rectangle(ART, 18, outline=(*color, 255), width=2)
    _tracked(draw, (WIDTH // 2, ART[1] + 34), KIND_LABELS[kind], 18, color)
    if equipped:
        label = "EQUIPADO"
        width = draw.textlength(label, font=font(16, True)) + 6 * len(label) + 28
        box = (ART[2] - 20 - width, ART[3] - 50, ART[2] - 20, ART[3] - 20)
        draw.rounded_rectangle(box, 15, fill=color)
        _tracked(draw, ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2), label, 16, BACKGROUND, 5)

    size = 42
    while size > 30 and draw.textlength(str(item["name"]), font=font(size, True)) > WIDTH - 70:
        size -= 2
    name = fit(draw, str(item["name"]), font(size, True), WIDTH - 70)
    text_center(draw, (WIDTH // 2, 616), name, size)
    text_center(
        draw, (WIDTH // 2, 664), f"de {owner}" if owner else "cosmético do clube", 22, False, MUTED
    )
    draw.line((WIDTH // 2 - 60, 698, WIDTH // 2 + 60, 698), fill=(*color, 255), width=2)
    bonus = "libera a renda do amistoso" if kind == "estadio" else "+250 no máximo do daily"
    text_center(draw, (WIDTH // 2, 730), bonus, 20, True, mix(color, (255, 255, 255), 0.35))
    return to_png(image)
