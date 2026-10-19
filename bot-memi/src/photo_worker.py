"""Coletor contínuo de fotos de jogadores do TheSportsDB."""

from __future__ import annotations

import argparse
import json
import logging
import time
from pathlib import Path

from src.db import DATABASE_PATH, connect, initialize_database
from src.photos import PHOTO_ROOT, PhotoCollector

DEFAULT_BATCH_SIZE = 30
DEFAULT_INTERVAL_SECONDS = 60
DEFAULT_CHECKPOINT = Path("data/photo_worker_checkpoint.json")
LOGGER = logging.getLogger("memi.photo_worker")


def _read_checkpoint(path: Path) -> int:
    if not path.exists():
        return 0
    try:
        value = json.loads(path.read_text(encoding="utf-8")).get("last_player_id", 0)
        return int(value)
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as error:
        raise RuntimeError(f"Checkpoint inválido: {path}") from error


def _write_checkpoint(path: Path, player_id: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps({"last_player_id": player_id}, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _batch_end_id(
    database_path: Path,
    after_id: int,
    batch_size: int,
) -> int | None:
    with connect(database_path) as connection:
        row = connection.execute(
            """
            SELECT MAX(id) AS last_id FROM (
                SELECT id FROM players
                WHERE photo_path IS NULL AND id > ?
                ORDER BY id
                LIMIT ?
            )
            """,
            (after_id, batch_size),
        ).fetchone()
    return int(row["last_id"]) if row and row["last_id"] is not None else None


def collect_forever(
    database_path: Path,
    photo_root: Path,
    checkpoint: Path,
    *,
    api_key: str,
    batch_size: int = DEFAULT_BATCH_SIZE,
    interval_seconds: int = DEFAULT_INTERVAL_SECONDS,
    once: bool = False,
) -> None:
    if batch_size < 1 or interval_seconds < 1:
        raise ValueError("batch-size e interval devem ser maiores que zero.")
    if not api_key.strip():
        raise ValueError("Configure THESPORTSDB_API_KEY no .env ou use --api-key.")

    initialize_database(database_path)
    collector = PhotoCollector(photo_root, thesportsdb_key=api_key)
    last_id = _read_checkpoint(checkpoint)
    LOGGER.info("Coleta iniciada após o jogador %s.", last_id)

    while True:
        started = time.monotonic()
        batch_end_id = _batch_end_id(database_path, last_id, batch_size)
        if batch_end_id is None:
            LOGGER.info("Não há mais jogadores sem foto após o ID %s.", last_id)
            return
        with connect(database_path) as connection:
            result = collector.collect_entity(
                connection,
                "players",
                True,
                batch_size,
                after_id=last_id,
            )
        last_id = batch_end_id
        _write_checkpoint(checkpoint, last_id)
        LOGGER.info(
            "Lote concluído: baixadas=%s ausentes=%s já existentes=%s; próximo ID=%s",
            result["downloaded"],
            result["missing"],
            result["already_present"],
            last_id,
        )
        if once:
            return
        time.sleep(max(0, interval_seconds - (time.monotonic() - started)))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DATABASE_PATH)
    parser.add_argument("--photo-root", type=Path, default=PHOTO_ROOT)
    parser.add_argument(
        "--checkpoint", type=Path, default=DEFAULT_CHECKPOINT
    )
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument("--interval", type=int, default=DEFAULT_INTERVAL_SECONDS)
    parser.add_argument("--api-key", help="chave do TheSportsDB; prefira o arquivo .env")
    parser.add_argument("--once", action="store_true", help="processa um lote e encerra")
    args = parser.parse_args()

    from dotenv import load_dotenv
    import os

    load_dotenv()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        collect_forever(
            args.database,
            args.photo_root,
            args.checkpoint,
            api_key=args.api_key or os.getenv("THESPORTSDB_API_KEY", ""),
            batch_size=args.batch_size,
            interval_seconds=args.interval,
            once=args.once,
        )
    except (OSError, RuntimeError, ValueError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
