"""Escudos reais dos times lendários do `mm!desafio`.

- **Clubes**: o escudo do clube de verdade, que o bot já baixou do TheSportsDB para a
  tabela `clubs` (procura pelo nome, preferindo o clube brasileiro).
- **Seleções**: o escudo da federação (CBF, FIGC…), buscado no TheSportsDB na primeira
  partida e guardado em `data/photos/selecoes/` para as próximas.
- **Seleção Mundial**: um escudo desenhado aqui (globo dourado com estrela), salvo uma vez.

Sem escudo encontrado (sem internet, sem chave do TheSportsDB…), o placar continua com o
escudo gerado de sempre. Cada busca que falha não se repete enquanto o bot estiver ligado.
"""

from __future__ import annotations

import logging
import math
import os
from pathlib import Path

from PIL import Image, ImageDraw

from src.db import DATABASE_PATH, connect, normalize_player_name
from src.encyclopedia import COUNTRIES_EN
from src.roll_images import PHOTO_ROOT

LOGGER = logging.getLogger("memi.pve_crests")
FOLDER = "selecoes"
PUBLIC_KEY = "123"
# Clube lendário -> pedaços do nome como o banco costuma escrever o clube de verdade.
CLUB_KEYS = {
    "Flamengo": ("flamengo",),
    "Palmeiras": ("palmeiras",),
    "Corinthians": ("corinthians",),
    "São Paulo": ("sao paulo",),
    "Santos": ("santos",),
    "Fluminense": ("fluminense",),
    "Vasco da Gama": ("vasco",),
    "Botafogo": ("botafogo",),
    "Grêmio": ("gremio",),
    "Internacional": ("internacional",),
    "Cruzeiro": ("cruzeiro",),
    "Atlético-MG": ("atletico mineiro", "atletico mg", "atletico-mg"),
}
BRAZIL = {"brasil", "brazil"}
_failed: set[str] = set()


def club_crest(club: str, database_path: str | Path = DATABASE_PATH) -> str | None:
    """Caminho do escudo do clube de verdade (relativo a `data/photos`) ou None."""
    keys = CLUB_KEYS.get(club, (normalize_player_name(club),))
    with connect(database_path) as connection:
        rows = connection.execute(
            "SELECT name, country, logo_path FROM clubs WHERE logo_path IS NOT NULL"
        ).fetchall()
    matches = []
    for row in rows:
        name = normalize_player_name(row["name"])
        if any(key in name for key in keys) and (PHOTO_ROOT / row["logo_path"]).is_file():
            brazilian = normalize_player_name(row["country"] or "") in BRAZIL
            # "Santos" não pode virar "Santos Laguna": brasileiro e nome curto primeiro.
            matches.append((not brazilian, len(name), row["logo_path"]))
    return min(matches)[2] if matches else None


def _sportsdb_badge(country: str) -> str | None:
    """URL do escudo da seleção (masculina, principal) no TheSportsDB."""
    from src.sportsdb_images import SportsDB, _first_image

    key = os.getenv("THESPORTSDB_API_KEY")
    if not key:
        from dotenv import load_dotenv

        from src.db import ROOT

        load_dotenv(ROOT / ".env")
        # Sem chave própria, a chave pública de teste do TheSportsDB (a antiga "3" saiu).
        key = os.getenv("THESPORTSDB_API_KEY") or PUBLIC_KEY
    english = COUNTRIES_EN.get(country, country)
    teams = SportsDB(key, interval=0).get("searchteams.php", t=english).get("teams") or []
    wanted = normalize_player_name(english)
    for team in teams:
        if (team.get("strSport") or "").lower() != "soccer":
            continue
        if (team.get("strGender") or "Male").lower() not in {"male", ""}:
            continue
        if normalize_player_name(team.get("strTeam") or "") != wanted:
            continue  # nada de "Brazil U20" ou clube com nome parecido
        badge = _first_image(team, ("strBadge", "strTeamBadge", "strLogo"))
        if badge:
            return badge
    return None


def national_crest(country: str) -> str | None:
    """Escudo da federação, baixado na primeira vez; None se não deu para achar."""
    slug = normalize_player_name(country).replace(" ", "-")
    for existing in sorted((PHOTO_ROOT / FOLDER).glob(f"{slug}.*")):
        return (Path(FOLDER) / existing.name).as_posix()
    if slug in _failed:
        return None
    try:
        url = _sportsdb_badge(country)
        if url:
            from src.photos import PhotoCollector

            return PhotoCollector()._download(url, FOLDER, slug).as_posix()
    except Exception:  # rede, limite, formato: fica com o escudo gerado
        LOGGER.warning("Não consegui o escudo da seleção %s.", country, exc_info=True)
    _failed.add(slug)
    return None


def world_crest() -> str:
    """Escudo da Seleção Mundial: globo dourado com estrela, desenhado uma vez."""
    relative = Path(FOLDER) / "selecao-mundial.png"
    path = PHOTO_ROOT / relative
    if path.is_file():
        return relative.as_posix()
    from src.visual import star

    size = 1024
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    gold, dark = (230, 181, 74, 255), (18, 22, 32, 255)
    c, r = size / 2, size * 0.46
    draw.ellipse((c - r, c - r, c + r, c + r), fill=dark, outline=gold, width=28)
    globe = r * 0.62
    line = 16
    draw.ellipse((c - globe, c - globe, c + globe, c + globe), outline=gold, width=line)
    meridian = globe * 0.45
    draw.ellipse((c - meridian, c - globe, c + meridian, c + globe), outline=gold, width=line)
    draw.line((c, c - globe, c, c + globe), fill=gold, width=line)
    draw.line((c - globe, c, c + globe, c), fill=gold, width=line)
    for latitude in (-0.5, 0.5):
        y = c + globe * latitude
        half = globe * math.cos(math.asin(abs(latitude)))
        draw.line((c - half, y, c + half, y), fill=gold, width=line)
    draw.polygon(star(c, c - r * 0.80, r * 0.13, r * 0.055), fill=gold)
    path.parent.mkdir(parents=True, exist_ok=True)
    image.resize((512, 512), Image.LANCZOS).save(path)
    return relative.as_posix()


def crest_for(team_key: str, database_path: str | Path = DATABASE_PATH) -> str | None:
    """Escudo de um time lendário pela chave (`clube:flamengo`, `selecao:brasil`, `mundial`).

    Chefões têm um sufixo na chave (`selecao:brasil:2026-W43`), que é ignorado."""
    kind, _, rest = team_key.partition(":")
    name = rest.split(":", 1)[0]
    if kind == "mundial":
        return world_crest()
    if kind == "clube":
        club = next((club for club in CLUB_KEYS if club.lower() == name), name)
        return club_crest(club, database_path)
    if kind == "selecao":
        country = next((c for c in COUNTRIES_EN if c.lower() == name), name.title())
        return national_crest(country)
    return None
