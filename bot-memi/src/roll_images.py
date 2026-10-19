"""Imagens usadas na apresentação de jogadores sorteados."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Any

import discord
from PIL import Image, ImageDraw

from src.db import ROOT

PLACEHOLDER_FILENAME = "silhueta.png"


def _safe_photo_path(photo_path: object) -> Path | None:
    if not isinstance(photo_path, str) or not photo_path.strip():
        return None
    candidate = Path(photo_path)
    if not candidate.is_absolute():
        candidate = ROOT / candidate
    try:
        candidate = candidate.resolve(strict=True)
        candidate.relative_to(ROOT.resolve())
    except (OSError, RuntimeError, ValueError):
        return None
    return candidate if candidate.is_file() else None


def _placeholder_file() -> discord.File:
    image = Image.new("RGBA", (360, 360), (35, 40, 55, 255))
    draw = ImageDraw.Draw(image)
    draw.ellipse((115, 48, 245, 178), fill=(180, 188, 205, 255))
    draw.ellipse((72, 145, 288, 390), fill=(180, 188, 205, 255))
    draw.ellipse((0, 0, 359, 359), outline=(90, 105, 135, 255), width=8)
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    buffer.seek(0)
    return discord.File(buffer, filename=PLACEHOLDER_FILENAME)


def roll_image_file(player: dict[str, Any]) -> discord.File:
    """Retorna a foto local do jogador ou uma silhueta de fallback."""
    photo_path = _safe_photo_path(player.get("photo_path"))
    if photo_path is not None:
        return discord.File(photo_path, filename=f"jogador{photo_path.suffix.lower()}")
    return _placeholder_file()
