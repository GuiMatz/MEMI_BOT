"""Artes prontas do FUT.AE (PNGs gerados por `scripts/renderizar_ilustracoes.py`)."""

from __future__ import annotations

from functools import lru_cache

from PIL import Image

from src.db import ROOT

ART_DIR = ROOT / "assets" / "ilustracoes"


@lru_cache(maxsize=32)
def artwork(name: str) -> Image.Image | None:
    """`emblema`, `tecnico`, `pacote`, `molduras/lendario`… ou None se faltar o arquivo."""
    path = ART_DIR / f"{name}.png"
    if not path.is_file():
        return None
    with Image.open(path) as opened:
        return opened.convert("RGBA")
