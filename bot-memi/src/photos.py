"""Baixa fotos, escudos e imagens de estádios a partir de URLs verificadas."""

from __future__ import annotations

import argparse
import os
import re
import unicodedata
from pathlib import Path
from urllib.parse import quote, urlparse

import requests
from dotenv import load_dotenv

from src.db import DATABASE_PATH, ROOT, connect, initialize_database

PHOTO_ROOT = ROOT / "data" / "photos"
THESPORTSDB_BASE = "https://www.thesportsdb.com/api/v1/json"
TIMEOUT = (5, 30)
MAX_IMAGE_BYTES = 10 * 1024 * 1024
IMAGE_EXTENSIONS = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
}
ENTITIES = {
    "players": {
        "name": "name",
        "url": "photo_url",
        "path": "photo_path",
        "search_key": "player",
        "result_name": "strPlayer",
        "result_url": ("strCutout", "strThumb"),
        "endpoint": "searchplayers.php",
        "parameter": "p",
    },
    "clubs": {
        "name": "name",
        "url": "logo_url",
        "path": "logo_path",
        "search_key": "teams",
        "result_name": "strTeam",
        "result_url": ("strTeamBadge", "strBadge", "strLogo"),
        "endpoint": "searchteams.php",
        "parameter": "t",
    },
    "stadiums": {
        "name": "name",
        "url": "photo_url",
        "path": "photo_path",
        "search_key": "venues",
        "result_name": "strVenue",
        "result_url": ("strThumb", "strFanart1"),
        "endpoint": "searchvenues.php",
        "parameter": "v",
    },
}


class ImageNotFound(Exception):
    """A origem não tem a imagem solicitada."""


def _normalize_name(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    normalized = "".join(
        character for character in normalized if not unicodedata.combining(character)
    )
    return re.sub(r"[^a-zA-Z0-9]+", " ", normalized).lower().strip()


class PhotoCollector:
    def __init__(
        self,
        photo_root: str | Path = PHOTO_ROOT,
        session: requests.Session | None = None,
        thesportsdb_key: str | None = None,
    ):
        load_dotenv(ROOT / ".env")
        self.photo_root = Path(photo_root)
        self.session = session or requests.Session()
        self.thesportsdb_key = thesportsdb_key or os.getenv("THESPORTSDB_API_KEY")

    def _sportsdb_image_url(self, entity: str, name: str) -> str | None:
        if not self.thesportsdb_key:
            raise ValueError("Para buscar imagens ausentes, configure THESPORTSDB_API_KEY no .env.")
        config = ENTITIES[entity]
        url = f"{THESPORTSDB_BASE}/{quote(self.thesportsdb_key, safe='')}/" f"{config['endpoint']}"
        try:
            response = self.session.get(
                url,
                params={config["parameter"]: name},
                timeout=TIMEOUT,
            )
        except requests.RequestException as error:
            raise RuntimeError(f"Falha ao consultar TheSportsDB para {entity}.") from error
        if response.status_code >= 400:
            raise RuntimeError(f"TheSportsDB respondeu HTTP {response.status_code} para {entity}.")
        try:
            payload = response.json()
        except ValueError as error:
            raise RuntimeError("Resposta JSON inválida do TheSportsDB.") from error

        expected_name = _normalize_name(name)
        for result in payload.get(config["search_key"]) or []:
            if _normalize_name(str(result.get(config["result_name"]) or "")) != expected_name:
                continue
            for key in config["result_url"]:
                image_url = result.get(key)
                if image_url:
                    return str(image_url)
        return None

    def _download(self, url: str, entity: str, entity_id: int) -> Path:
        parsed = urlparse(url)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ValueError(f"URL de imagem inválida para {entity} {entity_id}.")
        try:
            response = self.session.get(url, stream=True, timeout=TIMEOUT)
        except requests.RequestException as error:
            raise RuntimeError(
                f"Falha de rede ao baixar imagem de {entity} {entity_id}."
            ) from error
        try:
            if response.status_code == 404:
                raise ImageNotFound
            if response.status_code >= 400:
                raise RuntimeError(
                    f"HTTP {response.status_code} ao baixar imagem de " f"{entity} {entity_id}."
                )
            content_type = response.headers.get("Content-Type", "").split(";")[0].lower()
            extension = IMAGE_EXTENSIONS.get(content_type)
            if extension is None:
                raise ImageNotFound
            content_length = response.headers.get("Content-Length")
            if content_length and int(content_length) > MAX_IMAGE_BYTES:
                raise ValueError(f"Imagem muito grande para {entity} {entity_id}.")

            relative_path = Path(entity) / f"{entity_id}{extension}"
            destination = self.photo_root / relative_path
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = destination.with_suffix(destination.suffix + ".tmp")
            total = 0
            try:
                with temporary.open("wb") as file:
                    for chunk in response.iter_content(chunk_size=64 * 1024):
                        if not chunk:
                            continue
                        total += len(chunk)
                        if total > MAX_IMAGE_BYTES:
                            raise ValueError(f"Imagem excede 10 MB para {entity} {entity_id}.")
                        file.write(chunk)
                if total == 0:
                    raise ImageNotFound
                temporary.replace(destination)
            except requests.RequestException as error:
                temporary.unlink(missing_ok=True)
                raise RuntimeError(
                    f"Falha de rede ao ler imagem de {entity} {entity_id}."
                ) from error
            except Exception:
                temporary.unlink(missing_ok=True)
                raise
        finally:
            response.close()
        return relative_path

    def collect_entity(
        self,
        connection,
        entity: str,
        use_thesportsdb: bool,
        limit: int | None,
        after_id: int = 0,
    ) -> dict[str, int]:
        if entity not in ENTITIES:
            raise ValueError(f"Entidade de imagem não suportada: {entity}")
        config = ENTITIES[entity]
        rows = connection.execute(
            f"""
            SELECT id, {config['name']} AS name, {config['url']} AS image_url,
                   {config['path']} AS image_path
            FROM {entity}
            WHERE {config['path']} IS NULL AND id > ?
            ORDER BY id
            """,
            (after_id,),
        ).fetchall()
        result = {"downloaded": 0, "missing": 0, "already_present": 0}
        processed = 0
        for row in rows:
            if limit is not None and processed >= limit:
                break
            processed += 1
            existing_files = list((self.photo_root / entity).glob(f"{row['id']}.*"))
            existing_files = [
                path for path in existing_files if path.is_file() and not path.name.endswith(".tmp")
            ]
            if existing_files:
                existing_path = existing_files[0].relative_to(self.photo_root)
                connection.execute(
                    f"UPDATE {entity} SET {config['path']} = ? WHERE id = ?",
                    (existing_path.as_posix(), row["id"]),
                )
                result["already_present"] += 1
                continue
            image_url = row["image_url"]
            used_sportsdb = False
            if not image_url and use_thesportsdb:
                image_url = self._sportsdb_image_url(entity, row["name"])
                used_sportsdb = True
            if not image_url:
                result["missing"] += 1
                continue
            try:
                relative_path = self._download(image_url, entity, row["id"])
            except ImageNotFound:
                if not use_thesportsdb or used_sportsdb:
                    result["missing"] += 1
                    continue
                fallback_url = self._sportsdb_image_url(entity, row["name"])
                if not fallback_url or fallback_url == image_url:
                    result["missing"] += 1
                    continue
                try:
                    relative_path = self._download(fallback_url, entity, row["id"])
                except ImageNotFound:
                    result["missing"] += 1
                    continue
            connection.execute(
                f"UPDATE {entity} SET {config['path']} = ? WHERE id = ?",
                (relative_path.as_posix(), row["id"]),
            )
            result["downloaded"] += 1
        return result


def sync_existing_photos(
    database_path: str | Path = DATABASE_PATH,
    photo_root: str | Path = PHOTO_ROOT,
) -> int:
    """Liga ao banco as imagens já baixadas cujo caminho ainda não foi gravado."""
    root = Path(photo_root)
    updated = 0
    with connect(database_path) as connection:
        for entity, config in ENTITIES.items():
            folder = root / entity
            if not folder.is_dir():
                continue
            files: dict[int, str] = {}
            for path in sorted(folder.iterdir()):
                if (
                    path.is_file()
                    and path.stem.isdigit()
                    and path.suffix.casefold() in IMAGE_EXTENSIONS.values()
                ):
                    files.setdefault(int(path.stem), path.relative_to(root).as_posix())
            if not files:
                continue
            missing = [
                int(row["id"])
                for row in connection.execute(
                    f"SELECT id FROM {entity} WHERE {config['path']} IS NULL"
                )
                if int(row["id"]) in files
            ]
            connection.executemany(
                f"UPDATE {entity} SET {config['path']} = ? WHERE id = ?",
                [(files[entity_id], entity_id) for entity_id in missing],
            )
            updated += len(missing)
    return updated


def collect_photos(
    database_path: str | Path = DATABASE_PATH,
    photo_root: str | Path = PHOTO_ROOT,
    use_thesportsdb: bool = False,
    limit: int | None = None,
    session: requests.Session | None = None,
    thesportsdb_key: str | None = None,
) -> dict[str, dict[str, int]]:
    if limit is not None and limit < 1:
        raise ValueError("--limit deve ser um inteiro maior que zero.")
    if use_thesportsdb and not (thesportsdb_key or os.getenv("THESPORTSDB_API_KEY")):
        load_dotenv(ROOT / ".env")
        if not os.getenv("THESPORTSDB_API_KEY"):
            raise ValueError("Para buscar imagens ausentes, configure THESPORTSDB_API_KEY no .env.")
    initialize_database(database_path)
    collector = PhotoCollector(photo_root, session, thesportsdb_key)
    with connect(database_path) as connection:
        return {
            entity: collector.collect_entity(connection, entity, use_thesportsdb, limit)
            for entity in ENTITIES
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DATABASE_PATH)
    parser.add_argument("--photo-root", type=Path, default=PHOTO_ROOT)
    parser.add_argument("--limit", type=int, help="limita registros por tipo de entidade")
    parser.add_argument(
        "--thesportsdb",
        action="store_true",
        help="buscar por nome no TheSportsDB se a URL direta estiver ausente",
    )
    args = parser.parse_args()
    try:
        result = collect_photos(
            args.database,
            args.photo_root,
            args.thesportsdb,
            args.limit,
        )
    except (ValueError, RuntimeError, OSError) as error:
        parser.error(str(error))
    for entity, counts in result.items():
        print(f"{entity}: " + ", ".join(f"{key}={value}" for key, value in counts.items()))


if __name__ == "__main__":
    main()
