"""Medalhões e pacote da crate em SVG (fonte das artes renderizadas em PNG).

Linha minimalista: disco escuro chapado, aro fino na cor do tipo e o ícone em traço
fino (Tabler Icons, MIT), com um halo discreto. O pacote da crate é grafite com
contorno dourado e a aba de cima separada, para a animação de abertura.

Usado só por `scripts/renderizar_ilustracoes.py`; o bot lê os PNGs prontos.
"""

from __future__ import annotations

import re
from pathlib import Path

ICON_DIR = Path(__file__).resolve().parents[1] / "assets" / "ilustracoes" / "svg" / "icones"

# nome do PNG -> (ícone Tabler, cor de destaque)
MEDALLIONS = {
    "emblema": ("shield", "#e25c5c"),
    "uniforme": ("shirt-sport", "#4c8ff0"),
    "estadio": ("building-stadium", "#3cbf7a"),
    "tecnico": ("user-star", "#9c7cf0"),
    "liga": ("trophy", "#e8b84a"),
    "mascote": ("paw", "#f08a42"),
    "missoes": ("target-arrow", "#3fc6d8"),
    "temporada": ("calendar-stats", "#e8b84a"),
}


def icon_paths(name: str) -> str:
    """Os elementos de desenho do ícone, sem o quadrado invisível de 24×24."""
    svg = (ICON_DIR / f"{name}.svg").read_text(encoding="utf-8")
    elements = re.findall(r"<(?:path|circle|line|polyline|rect|ellipse)\b[^>]*/>", svg)
    return "".join(element for element in elements if 'stroke="none"' not in element)


def medallion_svg(icon: str, accent: str) -> str:
    """Disco escuro chapado, aro fino na cor do tipo e o ícone em traço fino."""
    size, c = 512, 256
    scale = 11
    offset = c - 12 * scale
    paths = icon_paths(icon)
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}">
  <defs>
    <radialGradient id="disc" cx="0.5" cy="0.35" r="0.75">
      <stop offset="0" stop-color="#1c2330"/><stop offset="1" stop-color="#11151d"/>
    </radialGradient>
    <radialGradient id="halo" cx="0.5" cy="0.5" r="0.5">
      <stop offset="0" stop-color="{accent}" stop-opacity="0.22"/>
      <stop offset="1" stop-color="{accent}" stop-opacity="0"/>
    </radialGradient>
    <linearGradient id="ink" gradientUnits="userSpaceOnUse" x1="0" y1="2" x2="0" y2="22">
      <stop offset="0" stop-color="#ffffff"/><stop offset="1" stop-color="{accent}"/>
    </linearGradient>
  </defs>
  <circle cx="{c}" cy="{c}" r="230" fill="url(#disc)"/>
  <circle cx="{c}" cy="{c}" r="226" fill="none" stroke="{accent}" stroke-width="4"/>
  <circle cx="{c}" cy="{c}" r="206" fill="none" stroke="#ffffff" stroke-opacity="0.08" stroke-width="1.5"/>
  <circle cx="{c}" cy="{c}" r="170" fill="url(#halo)"/>
  <g transform="translate({offset} {offset}) scale({scale})" fill="none" stroke="url(#ink)"
     stroke-width="1.15" stroke-linecap="round" stroke-linejoin="round">{paths}</g>
</svg>"""


def pack_svg() -> str:
    """Pacote da crate fechado; a aba de cima é desenhada à parte (`pack_top_svg`)."""
    return _pack(top=False)


def pack_top_svg() -> str:
    return _pack(top=True)


PACK_SIZE = (340, 480)
PACK_CUT = 140  # altura da aba que se solta na abertura


def _pack(top: bool) -> str:
    width, height = PACK_SIZE
    cut = PACK_CUT
    clip = (
        f'<rect x="0" y="0" width="{width}" height="{cut}"/>'
        if top
        else f'<rect x="0" y="{cut}" width="{width}" height="{height - cut}"/>'
    )
    shield = icon_paths("shield")
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}">
  <defs>
    <linearGradient id="body" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#232a37"/><stop offset="1" stop-color="#151a23"/>
    </linearGradient>
    <clipPath id="part">{clip}</clipPath>
  </defs>
  <g clip-path="url(#part)">
    <rect x="4" y="4" width="{width - 8}" height="{height - 8}" rx="18" fill="url(#body)"/>
    <rect x="4" y="4" width="{width - 8}" height="{height - 8}" rx="18" fill="none" stroke="#e6b54a" stroke-width="3"/>
    <rect x="18" y="18" width="{width - 36}" height="{height - 36}" rx="10" fill="none" stroke="#e6b54a" stroke-opacity="0.25" stroke-width="1"/>
    <line x1="18" y1="{cut}" x2="{width - 18}" y2="{cut}" stroke="#e6b54a" stroke-opacity="0.55" stroke-width="1.5" stroke-dasharray="6 6"/>
    <g transform="translate({width / 2 - 66} {height * 0.52 - 66}) scale(5.5)" fill="none" stroke="#e6b54a"
       stroke-width="1.1" stroke-linecap="round" stroke-linejoin="round">{shield}</g>
    <text x="{width / 2}" y="{height * 0.52 + 110}" text-anchor="middle" font-family="Arial, Helvetica, sans-serif"
          font-weight="700" font-size="24" letter-spacing="10" fill="#e6b54a">FUT.AE</text>
  </g>
</svg>"""


# Ícone Tabler de cada upgrade (emoji da loja).
UPGRADE_ICONS = {
    "rolls": "binoculars",
    "wishes": "eye",
    "star_wishes": "star",
    "daily": "clock",
    "wish_protection": "shield-check",
    "daily_max": "coins",
    "exclusivity": "lock",
    "dismiss_refund": "file-certificate",
    "claim_reset": "refresh",
    "box_office_cap": "ticket",
    "match_income": "chart-bar",
    "free_claim": "bolt",
    "roll_bank": "database",
}


def emoji_svg(icon: str, accent: str) -> str:
    """Emoji de upgrade: círculo chapado na cor do upgrade e ícone branco em traço firme
    (legível no tamanho pequeno em que o Discord mostra emojis)."""
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 128 128">
  <circle cx="64" cy="64" r="60" fill="{accent}"/>
  <circle cx="64" cy="64" r="60" fill="none" stroke="#000000" stroke-opacity="0.18" stroke-width="4"/>
  <g transform="translate(26 26) scale(3.1667)" fill="none" stroke="#ffffff" stroke-width="2"
     stroke-linecap="round" stroke-linejoin="round">{icon_paths(icon)}</g>
</svg>"""


def emoji_svgs() -> dict[str, str]:
    from src.perks import UPGRADES

    return {
        f"upg_{key}": emoji_svg(UPGRADE_ICONS[key], spec.color) for key, spec in UPGRADES.items()
    }


def art_svgs() -> dict[str, tuple[str, tuple[int, int]]]:
    """nome do PNG -> (SVG, tamanho)."""
    arts = {
        name: (medallion_svg(icon, accent), (512, 512))
        for name, (icon, accent) in MEDALLIONS.items()
    }
    arts["pacote"] = (pack_svg(), (340, 480))
    arts["pacote_topo"] = (pack_top_svg(), (340, 480))
    return arts
