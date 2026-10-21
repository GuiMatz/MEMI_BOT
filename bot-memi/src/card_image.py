"""Carta vertical do jogador (foto grande, overall, posição e nome), estilo Mudae."""

from __future__ import annotations

from io import BytesIO
from typing import Any

from PIL import Image, ImageDraw, ImageOps

from src.lineup_image import TIER_COLORS, _fit, _font
from src.ratings import RARITY_TIERS, rarity_index
from src.roll_images import find_image

WIDTH = 600
HEIGHT = 860
PHOTO_BOX = (24, 24, WIDTH - 24, 640)
BACKGROUND = (16, 20, 28)


def _silhouette(size: tuple[int, int], color: tuple[int, int, int]) -> Image.Image:
    width, height = size
    image = Image.new("RGBA", size, (30, 36, 48, 255))
    draw = ImageDraw.Draw(image)
    tone = tuple(int(0.3 * channel + 0.7 * base) for channel, base in zip(color, (70, 78, 95)))
    draw.ellipse((width * 0.32, height * 0.16, width * 0.68, height * 0.48), fill=tone)
    draw.ellipse((width * 0.14, height * 0.50, width * 0.86, height * 1.25), fill=tone)
    return image


def render_player_card(player: dict[str, Any]) -> BytesIO:
    overall = int(player.get("effective_overall") or player.get("overall") or 0)
    tier = rarity_index(overall) if overall else 0
    color = TIER_COLORS[tier or 0]
    image = Image.new("RGBA", (WIDTH, HEIGHT), BACKGROUND)
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((4, 4, WIDTH - 5, HEIGHT - 5), 28, outline=color, width=8)

    box_width = PHOTO_BOX[2] - PHOTO_BOX[0]
    box_height = PHOTO_BOX[3] - PHOTO_BOX[1]
    photo = find_image(player.get("photo_path"), "players", player.get("id"))
    picture = None
    if photo is not None:
        try:
            with Image.open(photo) as opened:
                picture = ImageOps.fit(
                    opened.convert("RGBA"), (box_width, box_height), centering=(0.5, 0.15)
                )
        except OSError:
            picture = None
    if picture is None:
        picture = _silhouette((box_width, box_height), color)
    mask = Image.new("L", (box_width, box_height), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, box_width - 1, box_height - 1), 22, fill=255)
    tile = Image.new("RGBA", (box_width, box_height), (24, 30, 40, 255))
    tile.alpha_composite(picture)
    image.paste(tile, PHOTO_BOX[:2], mask)

    # Selo de overall e posição no canto da foto.
    draw.rounded_rectangle((44, 44, 184, 214), 20, fill=(10, 14, 20, 225), outline=color, width=4)
    draw.text((114, 112), str(overall or "—"), font=_font(76, True), fill="white", anchor="mm")
    draw.text((114, 182), str(player.get("game_position") or "—"), font=_font(30, True), fill=color, anchor="mm")

    name = _fit(draw, str(player.get("name") or player.get("display_name") or "—"), _font(48, True), WIDTH - 60)
    draw.text((WIDTH // 2, 700), name, font=_font(48, True), fill="white", anchor="mm")
    club = _fit(draw, str(player.get("club_name") or "Passe livre"), _font(28), WIDTH - 80)
    draw.text((WIDTH // 2, 752), club, font=_font(28), fill=(185, 195, 210), anchor="mm")
    label = RARITY_TIERS[tier or 0][0].upper() if overall else "SEM NOTA"
    draw.rounded_rectangle((WIDTH // 2 - 120, 784, WIDTH // 2 + 120, 830), 23, fill=color)
    draw.text((WIDTH // 2, 807), label, font=_font(26, True), fill=(15, 15, 20), anchor="mm")

    buffer = BytesIO()
    image.convert("RGB").save(buffer, format="PNG", optimize=True)
    buffer.seek(0)
    return buffer
