"""Imagens usadas na apresentação de jogadores sorteados."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Any

import discord
from PIL import Image, ImageDraw

from src.db import ROOT

PLACEHOLDER_FILENAME = "silhueta.png"
PLACEHOLDER_PATH = ROOT / "data" / "placeholder.jpg"
PHOTO_ROOT = ROOT / "data" / "photos"
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif"}


def _safe_photo_path(photo_path: object) -> Path | None:
    if not isinstance(photo_path, str) or not photo_path.strip():
        return None
    candidate = Path(photo_path)
    candidates = (
        (candidate,) if candidate.is_absolute() else (ROOT / candidate, PHOTO_ROOT / candidate)
    )
    for candidate in candidates:
        try:
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(ROOT.resolve())
        except (OSError, RuntimeError, ValueError):
            continue
        if resolved.is_file():
            return resolved
    return None


def find_image(
    image_path: object,
    entity: str | None = None,
    entity_id: object = None,
) -> Path | None:
    """Usa o caminho salvo no banco ou, se faltar, o arquivo já baixado pelo worker."""
    found = _safe_photo_path(image_path)
    if found is not None or entity is None or entity_id is None:
        return found
    for candidate in sorted((PHOTO_ROOT / entity).glob(f"{entity_id}.*")):
        if candidate.is_file() and candidate.suffix.casefold() in IMAGE_SUFFIXES:
            return candidate
    return None


def image_file(path: Path, stem: str) -> discord.File:
    return discord.File(path, filename=f"{stem}{path.suffix.lower()}")


def _placeholder_file() -> discord.File:
    if PLACEHOLDER_PATH.is_file():
        return discord.File(PLACEHOLDER_PATH, filename="silhueta.jpg")
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
    photo_path = find_image(player.get("photo_path"), "players", player.get("id"))
    if photo_path is not None:
        return image_file(photo_path, "jogador")
    return _placeholder_file()
