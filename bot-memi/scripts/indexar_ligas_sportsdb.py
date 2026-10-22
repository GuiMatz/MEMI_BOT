"""Monta o índice de ligas de futebol do TheSportsDB (`src/seeds/sportsdb_ligas.json`).

A chave gratuita corta as listas de ligas (5 por país, 10 no total), mas a consulta por
ID (`lookupleague.php?id=`) devolve a liga completa. Este script percorre a faixa de IDs
de liga uma vez, com ~30 consultas por minuto, e guarda só as de futebol: ID, nome,
nomes alternativos, país e escudo. O coletor de imagens usa esse índice para achar as
ligas do bot sem depender das listas cortadas.

    python -m scripts.indexar_ligas_sportsdb                 # 4328 a 5800
    python -m scripts.indexar_ligas_sportsdb --inicio 5800 --fim 6500

Pode ser interrompido e rodado de novo: continua de onde parou.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

from src.db import ROOT

INDEX_PATH = ROOT / "src" / "seeds" / "sportsdb_ligas.json"
BASE = "https://www.thesportsdb.com/api/v1/json"


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--inicio", type=int, default=4328)
    parser.add_argument("--fim", type=int, default=5800)
    parser.add_argument("--saida", type=Path, default=INDEX_PATH)
    args = parser.parse_args()
    load_dotenv(ROOT / ".env")
    key = os.getenv("THESPORTSDB_API_KEY", "123")
    data = json.loads(args.saida.read_text(encoding="utf-8")) if args.saida.exists() else {}
    leagues = {int(item["id"]): item for item in data.get("leagues", [])}
    scanned = int(data.get("scanned_until", args.inicio - 1))
    session = requests.Session()
    for league_id in range(max(args.inicio, scanned + 1), args.fim + 1):
        for _ in range(4):
            try:
                response = session.get(
                    f"{BASE}/{key}/lookupleague.php", params={"id": league_id}, timeout=20
                )
            except requests.RequestException:
                time.sleep(15)
                continue
            if response.status_code == 429:
                time.sleep(60)
                continue
            break
        time.sleep(2.1)
        try:
            record = ((response.json() or {}).get("leagues") or [None])[0]
        except ValueError:
            record = None
        if record and str(record.get("strSport")) == "Soccer":
            leagues[league_id] = {
                "id": league_id,
                "name": record.get("strLeague"),
                "alternates": record.get("strLeagueAlternate") or "",
                "country": record.get("strCountry") or "",
                "badge": record.get("strBadge") or record.get("strLogo") or "",
            }
            print(league_id, record.get("strLeague"), "|", record.get("strCountry"), flush=True)
        if league_id % 25 == 0:
            _save(args.saida, leagues, league_id)
    _save(args.saida, leagues, args.fim)
    print(f"{len(leagues)} ligas de futebol no índice.")


def _save(path: Path, leagues: dict[int, dict], scanned: int) -> None:
    text = json.dumps(
        {"scanned_until": scanned, "leagues": [leagues[key] for key in sorted(leagues)]},
        ensure_ascii=False,
        indent=1,
    )
    path.write_bytes((text + "\n").encode("utf-8"))  # LF também no Windows


if __name__ == "__main__":
    main()
