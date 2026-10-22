"""Molduras das cartas por raridade, geradas como SVG e renderizadas em PNG.

Linha minimalista: traços finos, cores chapadas e só um pouco de luz. Cada série soma
um detalhe à anterior, sem relevo nem bisel:

- Base: borda simples. Comum: filete interno. Incomum: divisória no painel.
- Raro: marcas de canto. Épico: losangos vazados nas laterais.
- Lendário: chanfros angulares nos cantos, losango no topo e brilho suave.
- Ícone: dourado claro com pequenas estrelas.
- Galáctico: céu estrelado e nebulosa no painel.
- Única: supernova, um núcleo de luz no topo com raios, órbita e nebulosa carmesim.

A moldura cobre a carta inteira com um
recorte transparente onde entra a foto (`PHOTO_BOX`); o texto é escrito pelo
`card_image`. `scripts/renderizar_ilustracoes.py` transforma estes SVGs em PNG.
"""

from __future__ import annotations

import math
import random

FRAME_SIZE = (600, 860)
PHOTO_BOX = (24, 24, 576, 640)
TIER_KEYS = (
    "base",
    "comum",
    "incomum",
    "raro",
    "epico",
    "lendario",
    "icone",
    "galactico",
    "unica",
)

# (cores da borda, cor do painel de baixo, cor do brilho)
PALETTES = {
    "base": (("#8b929c", "#8b929c"), "#12161d", None),
    "comum": (("#4fcf86", "#4fcf86"), "#101a15", None),
    "incomum": (("#5b9cff", "#5b9cff"), "#0f1626", None),
    "raro": (("#b07cff", "#b07cff"), "#150f22", None),
    "epico": (("#ff9a3c", "#ff9a3c"), "#1c120a", None),
    "lendario": (("#ff4d5e", "#ffc65c"), "#1b0a0e", "#ff4d5e"),
    "icone": (("#fff4cf", "#e6b54a"), "#17130a", "#ffd76a"),
    "galactico": (("#5ff0ff", "#9b6bff"), "#0a0c1e", "#7a8cff"),
    "unica": (("#ffffff", "#ff9f1c", "#ff2e63"), "#07030b", "#ff9f1c"),
}


def _stops(colors: tuple[str, ...]) -> str:
    last = max(1, len(colors) - 1)
    return "".join(
        f'<stop offset="{index / last:.3f}" stop-color="{color}"/>'
        for index, color in enumerate(colors)
    )


def _window_path() -> str:
    x0, y0, x1, y1 = PHOTO_BOX
    r = 18
    width, height = FRAME_SIZE
    outer = (
        f"M28 0 H{width - 28} Q{width} 0 {width} 28 V{height - 28} Q{width} {height} "
        f"{width - 28} {height} H28 Q0 {height} 0 {height - 28} V28 Q0 0 28 0 Z"
    )
    window = (
        f"M{x0 + r} {y0} H{x1 - r} Q{x1} {y0} {x1} {y0 + r} V{y1 - r} Q{x1} {y1} {x1 - r} {y1} "
        f"H{x0 + r} Q{x0} {y1} {x0} {y1 - r} V{y0 + r} Q{x0} {y0} {x0 + r} {y0} Z"
    )
    return outer + " " + window


def _corners(content: str) -> str:
    """Repete um detalhe desenhado no canto superior esquerdo nos quatro cantos."""
    width, height = FRAME_SIZE
    return (
        f"<g>{content}</g>"
        f'<g transform="translate({width} 0) scale(-1 1)">{content}</g>'
        f'<g transform="translate(0 {height}) scale(1 -1)">{content}</g>'
        f'<g transform="translate({width} {height}) scale(-1 -1)">{content}</g>'
    )


def _stars(count: int, seed: int, area, color: str, max_r: float) -> str:
    generator = random.Random(seed)
    x0, y0, x1, y1 = area
    return "".join(
        f'<circle cx="{generator.uniform(x0, x1):.1f}" cy="{generator.uniform(y0, y1):.1f}" '
        f'r="{generator.uniform(0.5, max_r):.2f}" fill="{color}" '
        f'opacity="{generator.uniform(0.3, 0.95):.2f}"/>'
        for _ in range(count)
    )


def _twinkle(x: float, y: float, size: float, color: str) -> str:
    """Estrela de quatro pontas, chapada."""
    s, k = size, size * 0.18
    return (
        f'<path d="M{x} {y - s} L{x + k} {y - k} L{x + s} {y} L{x + k} {y + k} L{x} {y + s} '
        f'L{x - k} {y + k} L{x - s} {y} L{x - k} {y - k} Z" fill="{color}"/>'
    )


def frame_svg(tier: str) -> str:
    border, panel, glow = PALETTES[tier]
    level = TIER_KEYS.index(tier)
    width, height = FRAME_SIZE
    x0, y0, x1, y1 = PHOTO_BOX
    stroke = 6 if level < 5 else 7
    defs = [
        f'<linearGradient id="border" gradientUnits="userSpaceOnUse" x1="0" y1="0" '
        f'x2="{width}" y2="{height}">{_stops(border)}</linearGradient>',
        '<filter id="soft" x="-20%" y="-20%" width="140%" height="140%">'
        '<feGaussianBlur stdDeviation="10"/></filter>',
    ]
    body = [f'<path d="{_window_path()}" fill="{panel}" fill-rule="evenodd"/>']

    # Painel de baixo: estrelas, nebulosa e brilhos discretos.
    if tier == "icone":
        body.append(
            "".join(
                _twinkle(x, y, s, "#ffe7a0")
                for x, y, s in ((70, 700, 6), (530, 690, 7), (96, 806, 4), (508, 816, 5))
            )
        )
    if level >= 7:
        nebulae = (
            (("0.25", "0.4", "#6f4bff", "0.35"), ("0.8", "0.7", "#1fb6ff", "0.25"))
            if level == 7
            else (("0.25", "0.55", "#ff2e63", "0.35"), ("0.75", "0.35", "#ff9f1c", "0.3"))
        )
        for index, (cx, cy, color, opacity) in enumerate(nebulae):
            defs.append(
                f'<radialGradient id="nebula{index}" cx="{cx}" cy="{cy}" r="0.6">'
                f'<stop offset="0" stop-color="{color}" stop-opacity="{opacity}"/>'
                f'<stop offset="1" stop-color="{color}" stop-opacity="0"/></radialGradient>'
            )
            body.append(
                f'<rect x="0" y="{y1}" width="{width}" height="{height - y1}" '
                f'fill="url(#nebula{index})"/>'
            )
        body.append(_stars(70, level, (16, y1 + 8, width - 16, height - 16), "#ffffff", 1.5))

    if glow:
        body.append(
            f'<rect x="12" y="12" width="{width - 24}" height="{height - 24}" rx="26" fill="none" '
            f'stroke="{glow}" stroke-width="14" opacity="0.35" filter="url(#soft)"/>'
        )

    # Bordas: externa e da foto.
    half = stroke / 2
    body.append(
        f'<rect x="{half}" y="{half}" width="{width - stroke}" height="{height - stroke}" '
        f'rx="{28 - half}" fill="none" stroke="url(#border)" stroke-width="{stroke}"/>'
    )
    body.append(
        f'<rect x="{x0}" y="{y0}" width="{x1 - x0}" height="{y1 - y0}" rx="18" fill="none" '
        f'stroke="url(#border)" stroke-width="{2 if level < 5 else 2.5}"/>'
    )
    if level >= 1:
        inset = stroke + 6
        body.append(
            f'<rect x="{inset}" y="{inset}" width="{width - 2 * inset}" height="{height - 2 * inset}" '
            f'rx="{22 - half}" fill="none" stroke="url(#border)" stroke-opacity="0.35" stroke-width="1"/>'
        )
    if level >= 2:
        body.append(
            f'<line x1="200" y1="{y1 + 22}" x2="{width - 200}" y2="{y1 + 22}" '
            f'stroke="url(#border)" stroke-opacity="0.6" stroke-width="1.5"/>'
        )
    if level in (3, 4):
        tick = '<path d="M22 56 V22 H56" fill="none" stroke="url(#border)" stroke-width="3"/>'
        body.append(_corners(tick))
    if level == 4:
        for x in (stroke + 2, width - stroke - 2):
            body.append(
                f'<path d="M{x} {height / 2 + 112} l9 9 -9 9 -9 -9 Z" fill="{panel}" '
                f'stroke="url(#border)" stroke-width="2"/>'
            )
    if level >= 5:
        chamfer = (
            '<path d="M18 74 L74 18" stroke="url(#border)" stroke-width="3"/>'
            '<path d="M18 100 L100 18" stroke="url(#border)" stroke-width="1.5" stroke-opacity="0.6"/>'
        )
        body.append(_corners(chamfer))
        if level < 8:
            cx = width / 2
            body.append(
                f'<path d="M{cx} {y0 - 13} L{cx + 13} {y0} L{cx} {y0 + 13} L{cx - 13} {y0} Z" '
                f'fill="{panel}" stroke="url(#border)" stroke-width="2.5"/>'
                f'<path d="M{cx} {y0 - 6} L{cx + 6} {y0} L{cx} {y0 + 6} L{cx - 6} {y0} Z" '
                f'fill="url(#border)"/>'
            )
    if level >= 7:
        body.append(_stars(40, level + 3, (6, 4, width - 6, 18), "#ffffff", 1.2))
        body.append(_stars(40, level + 5, (6, height - 18, width - 6, height - 4), "#ffffff", 1.2))

    if level == 8:  # supernova
        cx, cy = width / 2, y0
        defs.append(
            '<radialGradient id="core" cx="0.5" cy="0.5" r="0.5">'
            '<stop offset="0" stop-color="#ffffff"/><stop offset="0.15" stop-color="#fff3c4"/>'
            '<stop offset="0.45" stop-color="#ff9f1c" stop-opacity="0.55"/>'
            '<stop offset="1" stop-color="#ff2e63" stop-opacity="0"/></radialGradient>'
        )
        rays = []
        for index in range(-1, 14):
            angle = index * math.pi / 12
            length = 110 if index % 2 else 170
            x, y = cx + length * math.cos(angle), cy + length * math.sin(angle)
            rays.append(
                f'<line x1="{cx}" y1="{cy}" x2="{x:.1f}" y2="{y:.1f}" stroke="#fff3c4" '
                f'stroke-width="{1.2 if index % 2 else 2}" stroke-opacity="{0.25 if index % 2 else 0.45}"/>'
            )
        body.append("".join(rays))
        body.append(
            f'<ellipse cx="{cx}" cy="{cy}" rx="150" ry="20" fill="none" stroke="#ffb347" '
            f'stroke-opacity="0.6" stroke-width="1.5" transform="rotate(-7 {cx} {cy})"/>'
        )
        body.append(f'<circle cx="{cx}" cy="{cy}" r="80" fill="url(#core)"/>')
        body.append(f'<circle cx="{cx}" cy="{cy}" r="9" fill="#ffffff"/>')

    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}">'
        f"<defs>{''.join(defs)}</defs>{''.join(body)}</svg>"
    )


def frame_svgs() -> dict[str, str]:
    svgs = {tier: frame_svg(tier) for tier in TIER_KEYS}
    return svgs
