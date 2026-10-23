"""Carta vertical do jogador (foto grande, overall, posição e nome), estilo Mudae."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageOps

from src.lineup_image import TIER_COLORS, _fit, _font
from src.card_frames import TIER_KEYS
from src.art import artwork
from src.ratings import CLUB_LEGEND_SOURCE, LEGEND_SOURCE, RARITY_TIERS, rarity_index
from src.roll_images import find_image

LEGEND_SOURCES = {LEGEND_SOURCE.casefold(), CLUB_LEGEND_SOURCE.casefold()}


def _legend_tag(image: Image.Image, color: tuple[int, int, int]) -> None:
    """Etiqueta discreta "LENDA" encaixada na borda de baixo da foto, como um recorte."""
    draw = ImageDraw.Draw(image)
    face = _font(14, True)
    text, spacing = "LENDA", 6
    widths = [draw.textlength(letter, font=face) for letter in text]
    width = sum(widths) + spacing * (len(text) - 1) + 36
    x0 = WIDTH / 2 - width / 2
    y = PHOTO_BOX[3]
    draw.rounded_rectangle(
        (x0, y - 13, x0 + width, y + 13), 13, fill=(12, 15, 21), outline=color, width=2
    )
    x = x0 + 18
    for letter, letter_width in zip(text, widths):
        draw.text((x, y), letter, font=face, fill=color, anchor="lm")
        x += letter_width + spacing


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


def render_player_card(player: dict[str, Any], picture: Image.Image | None = None) -> BytesIO:
    """Carta vertical do jogador; `picture` troca a foto (um quadro de GIF, por exemplo)."""
    return to_card_png(_card_image(player, picture))


def _card_image(player: dict[str, Any], picture: Image.Image | None = None) -> Image.Image:
    overall = int(player.get("effective_overall") or player.get("overall") or 0)
    tier = rarity_index(overall) if overall else 0
    color = TIER_COLORS[tier or 0]
    image = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))

    box_width = PHOTO_BOX[2] - PHOTO_BOX[0]
    box_height = PHOTO_BOX[3] - PHOTO_BOX[1]
    photo = find_image(player.get("photo_path"), "players", player.get("id"))
    if picture is not None:
        picture = ImageOps.fit(
            picture.convert("RGBA"), (box_width, box_height), centering=(0.5, 0.15)
        )
        photo = None
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

    # Moldura da série por cima da foto (só o recorte da foto é transparente).
    frame = artwork(f"molduras/{TIER_KEYS[tier or 0]}")
    if frame is not None:
        image.alpha_composite(frame)
    else:
        backdrop = Image.new("RGBA", (WIDTH, HEIGHT), BACKGROUND)
        backdrop.alpha_composite(image)
        image = backdrop
        ImageDraw.Draw(image).rounded_rectangle(
            (4, 4, WIDTH - 5, HEIGHT - 5), 28, outline=color, width=8
        )
    is_legend = str(player.get("overall_source") or "").casefold() in LEGEND_SOURCES
    if is_legend:
        _legend_tag(image, color)
    draw = ImageDraw.Draw(image)

    # Selo de overall e posição no canto da foto.
    badge = Image.new("RGBA", image.size, (0, 0, 0, 0))
    ImageDraw.Draw(badge).rounded_rectangle(
        (44, 44, 184, 214), 18, fill=(10, 14, 20, 200), outline=color, width=2
    )
    image.alpha_composite(badge)
    draw = ImageDraw.Draw(image)
    draw.text((114, 112), str(overall or "—"), font=_font(76, True), fill="white", anchor="mm")
    draw.text(
        (114, 182),
        str(player.get("game_position") or "—"),
        font=_font(30, True),
        fill=color,
        anchor="mm",
    )

    name = _fit(
        draw,
        str(player.get("name") or player.get("display_name") or "—"),
        _font(48, True),
        WIDTH - 60,
    )
    draw.text((WIDTH // 2, 700), name, font=_font(48, True), fill="white", anchor="mm")
    club = _fit(draw, str(player.get("club_name") or "Passe livre"), _font(28), WIDTH - 80)
    draw.text((WIDTH // 2, 752), club, font=_font(28), fill=(185, 195, 210), anchor="mm")
    label = RARITY_TIERS[tier or 0][0].upper() if overall else "SEM NOTA"
    draw.rounded_rectangle((WIDTH // 2 - 120, 784, WIDTH // 2 + 120, 830), 23, fill=color)
    draw.text((WIDTH // 2, 807), label, font=_font(26, True), fill=(15, 15, 20), anchor="mm")

    return image


def to_card_png(image: Image.Image) -> BytesIO:
    buffer = BytesIO()
    image.convert("RGB").save(buffer, format="PNG", compress_level=6)
    buffer.seek(0)
    return buffer


GIF_CARD_SCALE = 0.6  # carta animada menor: cabe folgado no limite de anexo
GIF_CARD_FRAMES = 48


def animated_photo(player: dict[str, Any]) -> Path | None:
    """Caminho da foto se ela for um GIF animado (aí a carta sai animada)."""
    photo = find_image(player.get("photo_path"), "players", player.get("id"))
    if photo is None or photo.suffix.casefold() != ".gif":
        return None
    try:
        with Image.open(photo) as opened:
            return photo if getattr(opened, "n_frames", 1) > 1 else None
    except OSError:
        return None


def render_player_card_gif(player: dict[str, Any]) -> BytesIO | None:
    """Carta animada quadro a quadro, para fotos em GIF; None se a foto é estática."""
    photo = animated_photo(player)
    if photo is None:
        return None
    size = (round(WIDTH * GIF_CARD_SCALE), round(HEIGHT * GIF_CARD_SCALE))
    frames, durations = [], []
    with Image.open(photo) as opened:
        total = opened.n_frames
        step = max(1, -(-total // GIF_CARD_FRAMES))  # pula quadros se forem muitos
        for index in range(0, total, step):
            opened.seek(index)
            card = _card_image(player, opened.convert("RGBA"))
            frames.append(card.convert("RGB").resize(size, Image.LANCZOS))
            duration = sum(
                _frame_duration(opened, frame) for frame in range(index, min(total, index + step))
            )
            durations.append(max(40, duration))
    paletted = [frame.quantize(colors=255, method=Image.Quantize.MEDIANCUT) for frame in frames]
    buffer = BytesIO()
    paletted[0].save(
        buffer,
        format="GIF",
        save_all=True,
        append_images=paletted[1:],
        duration=durations,
        loop=0,
        disposal=1,
    )
    buffer.seek(0)
    return buffer


def _frame_duration(image: Image.Image, frame: int) -> int:
    image.seek(frame)
    return int(image.info.get("duration") or 80)
