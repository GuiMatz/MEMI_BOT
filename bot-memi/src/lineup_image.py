"""Renderizador da escalação em PNG, com o desenho de cada formação."""

from __future__ import annotations

from functools import lru_cache
from io import BytesIO
from typing import Any

from PIL import Image, ImageDraw, ImageFont, ImageOps

from src.db import ROOT
from src.ratings import rarity_index
from src.roll_images import find_image
from src.teams import FORMATIONS

WIDTH = 900
HEIGHT = 1280
PITCH = (40, 150, WIDTH - 40, HEIGHT - 40)
GRASS = ((34, 118, 70), (39, 130, 77))
LINE = (226, 242, 230)
BACKGROUND = (17, 22, 30)
FONT_PATH = ROOT.parent / "assets" / "fonts" / "Manrope.ttf"
AVATAR = 96
# Profundidade de cada posição no campo (0 = defesa) e lado (0 esquerda, 2 direita).
DEPTH = {"ZAG": 0, "LE": 1, "LD": 1, "MC": 2, "MEI": 3, "PE": 4, "PD": 4, "CA": 5}
SIDE = {"LE": 0, "PE": 0, "LD": 2, "PD": 2}
# Cor do selo de overall por série de raridade (mesma ordem de RARITY_TIERS).
TIER_COLORS = (
    (150, 150, 150),
    (60, 170, 90),
    (60, 120, 210),
    (150, 80, 200),
    (230, 130, 40),
    (210, 50, 50),
    (240, 200, 60),
    (90, 220, 230),
    (255, 215, 0),
)


@lru_cache(maxsize=32)
def _font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    try:
        font = ImageFont.truetype(str(FONT_PATH), size)
        try:
            font.set_variation_by_axes([800 if bold else 600])
        except (OSError, AttributeError, ValueError):
            pass
        return font
    except OSError:
        return ImageFont.load_default(size=size)


def slot_coordinates(formation: str) -> dict[int, tuple[int, int]]:
    """Centro de cada vaga (1-11) da formação, do goleiro ao ataque."""
    if formation not in FORMATIONS:
        raise ValueError("A formação do time não é válida.")
    positions = FORMATIONS[formation]
    lines = [int(part) for part in formation.split("-")]
    outfield = sorted(
        (slot for slot in range(2, 12)),
        key=lambda slot: (DEPTH[positions[slot - 1]], slot),
    )
    left, top, right, bottom = PITCH
    usable_top, usable_bottom = top + 90, bottom - 290
    coordinates = {1: (WIDTH // 2, bottom - 120)}
    cursor = 0
    for line_index, count in enumerate(lines):
        members = outfield[cursor : cursor + count]
        cursor += count
        members.sort(key=lambda slot: (SIDE.get(positions[slot - 1], 1), slot))
        if len(lines) == 1:
            y = (usable_top + usable_bottom) // 2
        else:
            y = int(usable_bottom - (usable_bottom - usable_top) * line_index / (len(lines) - 1))
        spacing = (right - left) / (count + 1)
        for index, slot in enumerate(members, start=1):
            coordinates[slot] = (int(left + spacing * index), y)
    return coordinates


def _draw_pitch(image: Image.Image) -> None:
    draw = ImageDraw.Draw(image)
    left, top, right, bottom = PITCH
    stripe = (bottom - top) / 12
    for index in range(12):
        draw.rectangle(
            (left, int(top + index * stripe), right, int(top + (index + 1) * stripe)),
            fill=GRASS[index % 2],
        )
    draw.rectangle(PITCH, outline=LINE, width=4)
    middle = (top + bottom) // 2
    draw.line((left, middle, right, middle), fill=LINE, width=3)
    draw.ellipse(
        (WIDTH // 2 - 90, middle - 90, WIDTH // 2 + 90, middle + 90), outline=LINE, width=3
    )
    for y0, y1 in ((top, top + 150), (bottom - 150, bottom)):
        draw.rectangle((WIDTH // 2 - 190, y0, WIDTH // 2 + 190, y1), outline=LINE, width=3)
    for y0, y1 in ((top, top + 55), (bottom - 55, bottom)):
        draw.rectangle((WIDTH // 2 - 85, y0, WIDTH // 2 + 85, y1), outline=LINE, width=3)


def _avatar(player: dict[str, Any], color: tuple[int, int, int]) -> Image.Image:
    """Foto circular do jogador; sem foto, uma camisa na cor do time."""
    size = AVATAR
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size - 1, size - 1), fill=255)
    tile = Image.new("RGBA", (size, size), (28, 34, 44, 255))
    photo = find_image(player.get("photo_path"), "players", player.get("id"))
    if photo is not None:
        try:
            with Image.open(photo) as opened:
                fitted = ImageOps.fit(opened.convert("RGBA"), (size, size), centering=(0.5, 0.2))
                tile.alpha_composite(fitted)
        except OSError:
            photo = None
    if photo is None:
        draw = ImageDraw.Draw(tile)
        c = size // 2
        draw.polygon(
            [
                (c - 22, 24),
                (c - 44, 40),
                (c - 34, 56),
                (c - 26, 50),
                (c - 24, 84),
                (c + 24, 84),
                (c + 26, 50),
                (c + 34, 56),
                (c + 44, 40),
                (c + 22, 24),
                (c + 10, 30),
                (c - 10, 30),
            ],
            fill=color,
            outline=(255, 255, 255),
            width=2,
        )
    result = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    result.paste(tile, (0, 0), mask)
    ImageDraw.Draw(result).ellipse((1, 1, size - 2, size - 2), outline=(255, 255, 255), width=4)
    return result


def _centered(draw, xy, text, font, fill="white") -> None:
    box = draw.textbbox((0, 0), text, font=font)
    draw.text((xy[0] - (box[2] - box[0]) / 2, xy[1]), text, font=font, fill=fill)


def render_lineup(
    team: dict[str, Any],
    players: list[dict[str, Any]],
) -> BytesIO:
    """Desenha a formação escolhida; vagas vazias aparecem com a posição pedida."""
    formation = team["formation"]
    coordinates = slot_coordinates(formation)
    positions = FORMATIONS[formation]
    image = Image.new("RGBA", (WIDTH, HEIGHT), BACKGROUND)
    _draw_pitch(image)
    draw = ImageDraw.Draw(image)
    color = _rgb(team.get("color") or "#2E8B57")

    ratings = [
        int(player["effective_overall"]) for player in players if player.get("effective_overall")
    ]
    average = round(sum(ratings) / len(ratings)) if ratings else 0
    draw.rectangle((0, 0, WIDTH, 130), fill=(12, 16, 22))
    draw.rectangle((0, 124, WIDTH, 130), fill=color)
    logo_x = 40
    logo_path = team.get("logo_path")
    if logo_path and (ROOT / logo_path).is_file():
        with Image.open(ROOT / logo_path) as logo:
            logo = logo.convert("RGBA")
            logo.thumbnail((90, 90))
            image.alpha_composite(logo, (40, 18))
            logo_x = 145
    draw.text(
        (logo_x, 22),
        str(team.get("team_name") or "Meu time")[:28],
        font=_font(44, True),
        fill="white",
    )
    draw.text(
        (logo_x, 78),
        f"{formation}  ·  {len(players)}/11 titulares",
        font=_font(26),
        fill=(190, 200, 210),
    )
    if average:
        _centered(draw, (WIDTH - 85, 14), str(average), _font(56, True))
        _centered(draw, (WIDTH - 85, 82), "OVR MÉDIO", _font(18, True), fill=(190, 200, 210))

    by_slot = {int(player["slot_order"]): player for player in players}
    widths = _label_widths(coordinates)
    for slot, (x, y) in coordinates.items():
        position = positions[slot - 1]
        player = by_slot.get(slot)
        if player is None:
            radius = AVATAR // 2
            draw.ellipse(
                (x - radius, y - radius, x + radius, y + radius),
                fill=(20, 70, 42),
                outline=LINE,
                width=3,
            )
            draw.text((x, y), "+", font=_font(44, True), fill=LINE, anchor="mm")
            _centered(draw, (x, y + radius + 8), position, _font(24, True), fill=LINE)
            continue
        avatar = _avatar(player, color)
        image.alpha_composite(avatar, (x - AVATAR // 2, y - AVATAR // 2))
        rating = int(player.get("effective_overall") or player.get("overall") or 0)
        badge = TIER_COLORS[rarity_index(rating) or 0] if rating else (120, 120, 120)
        badge_font = _font(26, True)
        badge_width = draw.textlength(str(rating), font=badge_font) + 18
        draw.rounded_rectangle(
            (x + 14, y - 60, x + 14 + badge_width, y - 22), 10, fill=badge, outline="white", width=2
        )
        draw.text(
            (x + 14 + badge_width / 2, y - 41),
            str(rating),
            font=badge_font,
            fill="white",
            anchor="mm",
        )
        font = _font(20, True)
        name = _fit(draw, str(player.get("name") or player.get("display_name")), font, widths[slot])
        box = draw.textbbox((0, 0), name, font=font)
        half = (box[2] - box[0]) / 2 + 10
        draw.rounded_rectangle(
            (x - half, y + AVATAR // 2 + 4, x + half, y + AVATAR // 2 + 36),
            8,
            fill=(10, 14, 20, 230),
        )
        _centered(draw, (x, y + AVATAR // 2 + 7), name, font)
        _centered(draw, (x, y + AVATAR // 2 + 38), position, _font(18, True), fill=LINE)

    buffer = BytesIO()
    image.convert("RGB").save(buffer, format="PNG", optimize=True)
    buffer.seek(0)
    return buffer


def _label_widths(coordinates: dict[int, tuple[int, int]]) -> dict[int, float]:
    """Largura máxima do nome de cada vaga, para não encostar no vizinho da linha."""
    widths = {}
    for slot, (x, y) in coordinates.items():
        neighbours = [
            abs(x - other_x)
            for other, (other_x, other_y) in coordinates.items()
            if other != slot and abs(other_y - y) < 60
        ]
        widths[slot] = min([*neighbours, 260]) - 24
    return widths


def _fit(draw, text: str, font, width: float) -> str:
    if draw.textlength(text, font=font) <= width:
        return text
    while len(text) > 1 and draw.textlength(text + "…", font=font) > width:
        text = text[:-1]
    return text.rstrip() + "…"


def _rgb(value: str) -> tuple[int, int, int]:
    normalized = value.lstrip("#")
    if len(normalized) != 6:
        return (46, 139, 87)
    try:
        return tuple(int(normalized[index : index + 2], 16) for index in (0, 2, 4))
    except ValueError:
        return (46, 139, 87)
