"""Escudos reais dos times lendários do `mm!desafio`.

- **Clubes**: o escudo do clube de verdade, buscado no TheSportsDB pelo nome exato do clube
  e conferindo que é do Brasil (não depende da tabela `clubs`, que pode ter um homônimo).
- **Seleções**: o escudo da federação (CBF, FIGC…), buscado pelo nome exato do país.
- **Seleção Mundial**: um escudo desenhado aqui (globo dourado com estrela), salvo uma vez.

Clubes e seleções são baixados na primeira partida e guardados em `data/photos/` (pastas
`lendas_clubes/` e `selecoes/`) para as próximas. Sem escudo encontrado (sem internet, sem
chave do TheSportsDB…), o placar continua com o escudo gerado de sempre; a busca que falha
não se repete enquanto o bot estiver ligado.
"""

from __future__ import annotations

import logging
import math
import os
from pathlib import Path

from PIL import Image, ImageDraw

from src.db import DATABASE_PATH, ROOT, normalize_player_name
from src.encyclopedia import COUNTRIES_EN
from src.roll_images import PHOTO_ROOT

LOGGER = logging.getLogger("memi.pve_crests")
FOLDER = "selecoes"
CLUB_FOLDER = "lendas_clubes"
PUBLIC_KEY = "123"
# Os escudos de todos os times lendários já vêm no repositório (conferidos um a um): o bot
# não depende de internet nem do TheSportsDB para mostrá-los. A busca fica para times novos.
BUNDLED = ROOT / "assets" / "escudos"
# Clube lendário -> nome exato do clube no TheSportsDB (todos conferidos, do Brasil).
CLUB_SEARCH = {
    "Flamengo": "Flamengo",
    "Palmeiras": "Palmeiras",
    "Corinthians": "Corinthians",
    "São Paulo": "Sao Paulo",
    "Santos": "Santos",
    "Fluminense": "Fluminense",
    "Vasco da Gama": "Vasco da Gama",
    "Botafogo": "Botafogo",
    "Grêmio": "Gremio",
    "Internacional": "Internacional",
    "Cruzeiro": "Cruzeiro",
    "Atlético-MG": "Atletico Mineiro",
}
_failed: set[str] = set()


def _api_key() -> str:
    key = os.getenv("THESPORTSDB_API_KEY")
    if not key:
        from dotenv import load_dotenv

        from src.db import ROOT

        load_dotenv(ROOT / ".env")
        # Sem chave própria, a chave pública de teste do TheSportsDB (a antiga "3" saiu).
        key = os.getenv("THESPORTSDB_API_KEY") or PUBLIC_KEY
    return key


def _sportsdb_badge(name: str, country: str | None = None) -> str | None:
    """URL do escudo do time de futebol masculino chamado exatamente `name`.

    Com `country`, o time também precisa ser desse país (em inglês, como o TheSportsDB)."""
    from src.sportsdb_images import SportsDB, _first_image

    teams = SportsDB(_api_key(), interval=0).get("searchteams.php", t=name).get("teams") or []
    wanted = normalize_player_name(name)
    for team in teams:
        if (team.get("strSport") or "").lower() != "soccer":
            continue
        if (team.get("strGender") or "Male").lower() not in {"male", ""}:
            continue
        if normalize_player_name(team.get("strTeam") or "") != wanted:
            continue  # nada de "Brazil U20", "Santos Laguna" ou nome parecido
        if country and normalize_player_name(team.get("strCountry") or "") != normalize_player_name(
            country
        ):
            continue
        badge = _first_image(team, ("strBadge", "strTeamBadge", "strLogo"))
        if badge:
            return badge
    return None


def _cached_crest(folder: str, slug: str, name: str, country: str | None) -> str | None:
    """Escudo que vem no repositório, já baixado em `folder/slug.*` ou baixado agora.

    None se não deu."""
    for bundled in sorted((BUNDLED / folder).glob(f"{slug}.*")):
        return bundled.relative_to(ROOT).as_posix()
    for existing in sorted((PHOTO_ROOT / folder).glob(f"{slug}.*")):
        return (Path(folder) / existing.name).as_posix()
    if f"{folder}/{slug}" in _failed:
        return None
    try:
        url = _sportsdb_badge(name, country)
        if url:
            from src.photos import PhotoCollector

            return PhotoCollector(photo_root=PHOTO_ROOT)._download(url, folder, slug).as_posix()
    except Exception:  # rede, limite, formato: fica com o escudo gerado
        LOGGER.warning("Não consegui o escudo de %s.", name, exc_info=True)
    _failed.add(f"{folder}/{slug}")
    return None


def _slug(value: str) -> str:
    return normalize_player_name(value).replace(" ", "-")


def club_crest(club: str) -> str | None:
    """Escudo do clube brasileiro de verdade (relativo a `data/photos`) ou None."""
    return _cached_crest(CLUB_FOLDER, _slug(club), CLUB_SEARCH.get(club, club), "Brazil")


def national_crest(country: str) -> str | None:
    """Escudo da federação, baixado na primeira vez; None se não deu para achar."""
    return _cached_crest(FOLDER, _slug(country), COUNTRIES_EN.get(country, country), None)


def world_crest() -> str:
    """Escudo da Seleção Mundial: globo dourado com estrela, desenhado uma vez."""
    if (BUNDLED / FOLDER / "selecao-mundial.png").is_file():
        return (BUNDLED / FOLDER / "selecao-mundial.png").relative_to(ROOT).as_posix()
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
        club = next((club for club in CLUB_SEARCH if club.lower() == name), name)
        return club_crest(club)
    if kind == "selecao":
        country = next((c for c in COUNTRIES_EN if c.lower() == name), name.title())
        return national_crest(country)
    return None
