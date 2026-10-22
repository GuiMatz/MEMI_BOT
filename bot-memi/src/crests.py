"""Escudo custom: cada pessoa pode enviar a própria imagem como escudo do time.

O escudo enviado fica em `data/photos/escudos/<discord_id>.png` e na coluna
`game_teams.custom_logo_path`, separado do emblema da loja (`logo_path`): equipar ou
trocar emblemas não apaga o escudo custom, e remover o custom volta para o emblema
equipado. A imagem é normalizada antes de salvar (primeiro quadro, bordas transparentes
cortadas, no máximo 512 px), então qualquer formato comum serve.
"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image, UnidentifiedImageError

from src.db import DATABASE_PATH, connect
from src.roll_images import PHOTO_ROOT

CREST_FOLDER = "escudos"
CREST_SIZE = 512
MIN_SIDE = 48
MAX_BYTES = 8 * 1024 * 1024
MAX_PIXELS = 6000 * 6000


class CrestError(ValueError):
    """Imagem recusada ou time inexistente; a mensagem vai direto para a pessoa."""


def team_logo_path(team: dict[str, Any]) -> str | None:
    """Escudo custom, se houver; senão, o emblema equipado."""
    return team.get("custom_logo_path") or team.get("logo_path")


def normalize_crest(data: bytes) -> Image.Image:
    if len(data) > MAX_BYTES:
        raise CrestError("A imagem passa de 8 MB. Envie uma menor.")
    try:
        with Image.open(BytesIO(data)) as opened:
            if opened.width * opened.height > MAX_PIXELS:
                raise CrestError("A imagem é grande demais. Envie uma de até 6000 × 6000.")
            opened.seek(0)
            picture = opened.convert("RGBA")
    except (UnidentifiedImageError, OSError) as error:
        raise CrestError("Não consegui abrir essa imagem. Use PNG, JPG, WEBP ou GIF.") from error
    box = picture.getchannel("A").getbbox()
    if box is None:
        raise CrestError("A imagem está totalmente transparente.")
    picture = picture.crop(box)
    if min(picture.size) < MIN_SIDE:
        raise CrestError(f"A imagem é pequena demais (mínimo {MIN_SIDE} px de cada lado).")
    picture.thumbnail((CREST_SIZE, CREST_SIZE), Image.LANCZOS)
    return picture


def save_custom_crest(
    discord_user_id: int | str,
    data: bytes,
    database_path: str | Path = DATABASE_PATH,
    *,
    root: Path = PHOTO_ROOT,
) -> Path:
    """Valida, normaliza e grava o escudo; devolve o arquivo salvo."""
    user_id = str(int(discord_user_id))  # só dígitos no nome do arquivo
    picture = normalize_crest(data)
    with connect(database_path) as connection:
        exists = connection.execute(
            "SELECT 1 FROM game_teams WHERE discord_user_id = ?", (user_id,)
        ).fetchone()
        if exists is None:
            raise CrestError("Configure seu time primeiro com `mm!time`.")
        folder = root / CREST_FOLDER
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / f"{user_id}.png"
        picture.save(target, format="PNG", optimize=True)
        connection.execute(
            "UPDATE game_teams SET custom_logo_path = ?, updated_at = CURRENT_TIMESTAMP "
            "WHERE discord_user_id = ?",
            (f"{CREST_FOLDER}/{user_id}.png", user_id),
        )
    return target


def remove_custom_crest(
    discord_user_id: int | str,
    database_path: str | Path = DATABASE_PATH,
    *,
    root: Path = PHOTO_ROOT,
) -> bool:
    """Tira o escudo custom (o time volta ao emblema equipado). False se não havia."""
    user_id = str(int(discord_user_id))
    with connect(database_path) as connection:
        cursor = connection.execute(
            "UPDATE game_teams SET custom_logo_path = NULL, updated_at = CURRENT_TIMESTAMP "
            "WHERE discord_user_id = ? AND custom_logo_path IS NOT NULL",
            (user_id,),
        )
    (root / CREST_FOLDER / f"{user_id}.png").unlink(missing_ok=True)
    return cursor.rowcount > 0
