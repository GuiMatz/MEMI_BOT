"""Galeria de imagens dos jogadores (`mm!img`), como no Mudae.

Cada jogador pode ter várias imagens; a **principal** é a que vai na carta (a coluna
`players.photo_path`). A foto original (baixada pelo coletor) entra na galeria como a
primeira opção na hora em que alguém adiciona outra imagem, então dá sempre para voltar
a ela. Imagens novas ficam em `data/photos/players_extra/<jogador>-<imagem>.png`,
cortadas na proporção da foto da carta (GIF animado continua animado, até
`MAX_GIF_FRAMES` quadros) — assim qualquer imagem sempre parece carta.

Quem pode adicionar, trocar a principal e apagar: o dono do jogador ou a moderação
(a regra fica no comando; aqui só a mecânica).
"""

from __future__ import annotations

import os
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps, UnidentifiedImageError

from src.db import DATABASE_PATH, connect
from src.roll_images import PHOTO_ROOT, find_image

FOLDER = "players_extra"
MAX_IMAGES = 10
# Proporção da foto na carta (`src.card_image.PHOTO_BOX`, 552 × 616): toda imagem é
# cortada nela, então sempre parece carta. GIF fica menor para caber no Discord.
CARD_SIZE = (552, 616)
GIF_SIZE = (414, 462)
CARD_CENTERING = (0.5, 0.15)
MAX_GIF_FRAMES = 60
MIN_SIDE = 100
MAX_BYTES = 8 * 1024 * 1024


class ImageError(ValueError):
    """Imagem recusada; a mensagem vai direto para a pessoa."""


def _relative(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(path)


def _original_file(photo_path, player_id: int, root: Path) -> Path | None:
    """A foto atual da carta: o caminho gravado ou o arquivo baixado `players/<id>.*`."""
    found = find_image(photo_path, "players", player_id)
    if found is not None:
        return found
    if photo_path:
        for candidate in (Path(photo_path), root / str(photo_path)):
            if candidate.is_file():
                return candidate
    return next((path for path in sorted((root / "players").glob(f"{player_id}.*"))), None)


def _ensure_original(connection, player_id: int, root: Path) -> None:
    """Guarda a foto atual como "original" antes da primeira imagem nova."""
    if connection.execute(
        "SELECT 1 FROM player_images WHERE player_id = ? LIMIT 1", (player_id,)
    ).fetchone():
        return
    row = connection.execute("SELECT photo_path FROM players WHERE id = ?", (player_id,)).fetchone()
    if row is None:
        raise ImageError("Jogador não encontrado.")
    found = _original_file(row["photo_path"], player_id, root)
    if found is None:
        return
    connection.execute(
        "INSERT INTO player_images (player_id, image_path, source) VALUES (?, ?, 'original')",
        (player_id, _relative(found, root)),
    )


def list_images(
    player_id: int, database_path: str | Path = DATABASE_PATH, *, root: Path = PHOTO_ROOT
) -> list[dict[str, Any]]:
    """Imagens do jogador, a original primeiro; `main` marca a que está na carta."""
    with connect(database_path) as connection:
        _ensure_original(connection, player_id, root)
        main = connection.execute(
            "SELECT photo_path FROM players WHERE id = ?", (player_id,)
        ).fetchone()
        rows = connection.execute(
            "SELECT id, image_path, source, added_by, created_at FROM player_images "
            "WHERE player_id = ? ORDER BY source = 'custom', id",
            (player_id,),
        ).fetchall()
    images = [dict(row) for row in rows]
    current = main["photo_path"] if main else None
    for image in images:
        image["main"] = image["image_path"] == current
    if images and images[0]["source"] == "original" and not any(i["main"] for i in images):
        images[0]["main"] = True  # foto baixada sem caminho gravado: é a original
    return images


def _card_crop(frame: Image.Image, size: tuple[int, int]) -> Image.Image:
    """Corta na proporção da foto da carta, puxando para cima (onde fica o rosto)."""
    return ImageOps.fit(frame, size, Image.LANCZOS, centering=CARD_CENTERING)


def normalize_image(data: bytes) -> tuple[bytes, str]:
    """Imagem pronta para a carta: (bytes, extensão). GIF animado continua animado."""
    if len(data) > MAX_BYTES:
        raise ImageError("A imagem passa de 8 MB. Envie uma menor.")
    try:
        with Image.open(BytesIO(data)) as opened:
            if opened.width * opened.height > 6000 * 6000:
                raise ImageError("A imagem é grande demais.")
            if min(opened.size) < MIN_SIDE:
                raise ImageError(f"A imagem é pequena demais (mínimo {MIN_SIDE} px de cada lado).")
            total = getattr(opened, "n_frames", 1)
            buffer = BytesIO()
            if total > 1:
                step = max(1, -(-total // MAX_GIF_FRAMES))
                frames, durations = [], []
                for index in range(0, total, step):
                    opened.seek(index)
                    frame = _card_crop(opened.convert("RGBA"), GIF_SIZE).convert("RGB")
                    frames.append(frame.quantize(colors=255, method=Image.Quantize.MEDIANCUT))
                    duration = 0
                    for skipped in range(index, min(total, index + step)):
                        opened.seek(skipped)
                        duration += int(opened.info.get("duration") or 80)
                    durations.append(max(40, duration))
                frames[0].save(
                    buffer,
                    format="GIF",
                    save_all=True,
                    append_images=frames[1:],
                    duration=durations,
                    loop=0,
                    disposal=2,
                )
                return buffer.getvalue(), "gif"
            opened.seek(0)
            _card_crop(opened.convert("RGB"), CARD_SIZE).save(buffer, format="PNG", optimize=True)
            return buffer.getvalue(), "png"
    except (UnidentifiedImageError, OSError) as error:
        raise ImageError("Não consegui abrir essa imagem. Use PNG, JPG, WEBP ou GIF.") from error


def add_image(
    player_id: int,
    data: bytes,
    added_by: int | str,
    database_path: str | Path = DATABASE_PATH,
    *,
    root: Path = PHOTO_ROOT,
) -> dict[str, Any]:
    """Cadastra a imagem e devolve o registro dela.

    Se o jogador ainda não tinha foto nenhuma, a imagem já vira a principal da carta;
    se tinha, a principal continua a mesma até alguém escolher outra (⭐).
    """
    content, extension = normalize_image(data)
    with connect(database_path) as connection:
        _ensure_original(connection, player_id, root)
        count = connection.execute(
            "SELECT COUNT(*) AS n FROM player_images WHERE player_id = ?", (player_id,)
        ).fetchone()["n"]
        if count >= MAX_IMAGES:
            raise ImageError(f"Esse jogador já tem {MAX_IMAGES} imagens. Apague uma antes.")
        image_id = connection.execute(
            "INSERT INTO player_images (player_id, image_path, added_by) VALUES (?, '', ?)",
            (player_id, str(added_by)),
        ).lastrowid
        folder = root / FOLDER
        folder.mkdir(parents=True, exist_ok=True)
        relative = f"{FOLDER}/{int(player_id)}-{int(image_id)}.{extension}"
        (root / relative).write_bytes(content)
        connection.execute(
            "UPDATE player_images SET image_path = ? WHERE id = ?", (relative, image_id)
        )
        current = connection.execute(
            "SELECT photo_path FROM players WHERE id = ?", (player_id,)
        ).fetchone()["photo_path"]
        main = _original_file(current, player_id, root) is None
        if main:
            connection.execute(
                "UPDATE players SET photo_path = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (relative, player_id),
            )
    return {"id": image_id, "image_path": relative, "source": "custom", "main": main}


def set_main(player_id: int, image_id: int, database_path: str | Path = DATABASE_PATH) -> None:
    with connect(database_path) as connection:
        row = connection.execute(
            "SELECT image_path FROM player_images WHERE id = ? AND player_id = ?",
            (image_id, player_id),
        ).fetchone()
        if row is None:
            raise ImageError("Essa imagem não é desse jogador.")
        connection.execute(
            "UPDATE players SET photo_path = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (row["image_path"], player_id),
        )


def delete_image(
    player_id: int,
    image_id: int,
    database_path: str | Path = DATABASE_PATH,
    *,
    root: Path = PHOTO_ROOT,
) -> None:
    """Apaga uma imagem nova; se era a principal, a carta volta para a original."""
    with connect(database_path) as connection:
        row = connection.execute(
            "SELECT image_path, source FROM player_images WHERE id = ? AND player_id = ?",
            (image_id, player_id),
        ).fetchone()
        if row is None:
            raise ImageError("Essa imagem não é desse jogador.")
        if row["source"] == "original":
            raise ImageError("A foto original não pode ser apagada.")
        connection.execute("DELETE FROM player_images WHERE id = ?", (image_id,))
        current = connection.execute(
            "SELECT photo_path FROM players WHERE id = ?", (player_id,)
        ).fetchone()["photo_path"]
        if current == row["image_path"]:
            original = connection.execute(
                "SELECT image_path FROM player_images WHERE player_id = ? AND source = 'original'",
                (player_id,),
            ).fetchone()
            connection.execute(
                "UPDATE players SET photo_path = ? WHERE id = ?",
                (original["image_path"] if original else None, player_id),
            )
    (root / row["image_path"]).unlink(missing_ok=True)


def _photo_files(root: Path) -> set[int]:
    """Ids que têm arquivo baixado em `players/<id>.*` (uma leitura da pasta)."""
    folder = root / "players"
    if not folder.is_dir():
        return set()
    return {
        int(entry.name.split(".", 1)[0])
        for entry in os.scandir(folder)
        if entry.is_file() and entry.name.split(".", 1)[0].isdigit()
    }


def missing_photos(
    database_path: str | Path = DATABASE_PATH,
    *,
    owner_id: int | str | None = None,
    root: Path = PHOTO_ROOT,
) -> tuple[list[int], int]:
    """Jogadores do mercado sem foto, do maior overall para o menor, e o total do mercado.

    Com `owner_id`, só os do elenco dessa pessoa.
    """
    from src.pool_cache import snapshot  # o índice do mercado importa o banco

    files = _photo_files(root)
    with connect(database_path) as connection:
        pool = snapshot(connection, database_path)
        paths = {
            int(row["id"]): row["photo_path"]
            for row in connection.execute(
                "SELECT id, photo_path FROM players WHERE photo_path IS NOT NULL"
            )
        }
        owned = None
        if owner_id is not None:
            owned = {
                int(row["player_id"])
                for row in connection.execute(
                    "SELECT player_id FROM player_ownership WHERE discord_user_id = ?",
                    (str(owner_id),),
                )
            }

    def has_photo(player_id: int) -> bool:
        if player_id in files:
            return True
        path = paths.get(player_id)
        return bool(path) and _original_file(path, player_id, root) is not None

    candidates = pool.ranking if owned is None else [i for i in pool.ranking if i in owned]
    missing = [player_id for player_id in candidates if not has_photo(player_id)]
    return missing, len(candidates)
