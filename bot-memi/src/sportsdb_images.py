"""Coleta imagens do TheSportsDB para ligas, técnicos, estádios, lendas e times novos.

Cada imagem é salva com o ID do registro, igual às fotos dos jogadores:
`data/photos/<tipo>/<id>.<ext>` (tipos: `leagues`, `coaches`, `stadiums`, `players`,
`clubs`), e o caminho é gravado no banco. O bot liga sozinho, na inicialização,
qualquer arquivo que já esteja nessas pastas.

- **ligas**: lista as ligas de futebol de cada país e compara pelo nome;
- **técnicos**: busca pelo nome (no TheSportsDB eles ficam na base de jogadores);
- **estádios**: busca pelo nome do estádio;
- **lendas**: jogadores `Lenda`/`Lenda de Clube` sem foto, pelo nome e pelos apelidos,
  conferindo a nacionalidade para não pegar um homônimo;
- **times**: lista os times de cada liga encontrada. Time que já existe no bot (mesmo
  nome, ignorando FC/SC/acentos) só ganha o escudo se ainda não tiver; time novo é
  criado com o escudo. Nada é duplicado.

Quem não foi encontrado fica registrado em `image_attempts` e não é buscado de novo
(use `--retentar` para tentar outra vez).

    python -m src.sportsdb_images                      # tudo
    python -m src.sportsdb_images --tipos ligas,lendas --limite 20
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import time
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv

from src.db import DATABASE_PATH, ROOT, connect, initialize_database, normalize_player_name
from src.photos import PHOTO_ROOT, THESPORTSDB_BASE, TIMEOUT, ImageNotFound, PhotoCollector

LOGGER = logging.getLogger("memi.sportsdb_images")
TYPES = ("ligas", "tecnicos", "estadios", "lendas", "times")
# O plano gratuito aceita ~30 consultas por minuto.
MIN_INTERVAL = 2.1
LEGEND_SOURCES = ("Lenda", "Lenda de Clube")
# Sufixos de apelido que o TheSportsDB costuma não usar ("Ronaldo Fenômeno" -> "Ronaldo").
NICKNAME_SUFFIXES = {"fenomeno", "gaucho", "carioca", "maravilha", "batata", "caju"}


def _key(value: str | None) -> str:
    return normalize_player_name(value or "")


VENUE_NOISE = {"estadio", "estadi", "stadium", "stade", "stadio", "do", "da", "de", "del", "the"}


def _venue_key(value: str | None) -> str:
    words = [word for word in _key(value).split() if word not in VENUE_NOISE]
    return " ".join(words) or _key(value)


class SportsDB:
    """Cliente do TheSportsDB com intervalo mínimo entre consultas e nova tentativa no 429."""

    def __init__(
        self, api_key: str, session: requests.Session | None = None, interval: float = MIN_INTERVAL
    ):
        self.api_key = api_key
        self.session = session or requests.Session()
        self.interval = interval
        self._last = 0.0

    def get(self, endpoint: str, **params: str) -> dict[str, Any]:
        url = f"{THESPORTSDB_BASE}/{self.api_key}/{endpoint}"
        for attempt in range(4):
            wait = self.interval - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
            try:
                response = self.session.get(url, params=params, timeout=TIMEOUT)
            except requests.RequestException as error:
                LOGGER.warning("Falha de rede em %s (%s); nova tentativa.", endpoint, error)
                time.sleep(10 * (attempt + 1))
                continue
            if response.status_code == 429:
                LOGGER.info("Limite do TheSportsDB atingido; esperando 60 s.")
                time.sleep(60)
                continue
            if response.status_code >= 400:
                raise RuntimeError(
                    f"TheSportsDB respondeu HTTP {response.status_code} em {endpoint}."
                )
            try:
                return response.json() or {}
            except ValueError:
                return {}
        raise RuntimeError(f"TheSportsDB não respondeu em {endpoint} depois de várias tentativas.")


def _first_image(record: dict[str, Any], keys: Iterable[str]) -> str | None:
    for key in keys:
        value = record.get(key)
        if value and str(value).startswith("https://"):
            return str(value)
    return None


def _country_names(country: str | None) -> list[str]:
    """Nome do país como o TheSportsDB usa (os do banco podem estar em português)."""
    from src.encyclopedia import COUNTRIES_EN

    if not country:
        return []
    english = COUNTRIES_EN.get(country, country)
    names = [english]
    names += {
        "Türkiye": ["Turkey"],
        "Korea, South": ["South Korea"],
        "Cote d'Ivoire": ["Ivory Coast"],
    }.get(english, [])
    return names


class ImageHarvester:
    def __init__(
        self,
        database_path: str | Path,
        api: SportsDB,
        photo_root: str | Path = PHOTO_ROOT,
        *,
        retry: bool = False,
        limit: int | None = None,
    ):
        self.database_path = database_path
        self.api = api
        self.downloader = PhotoCollector(photo_root, api.session, api.api_key)
        self.retry = retry
        self.limit = limit
        self._leagues_by_country: dict[str, list[dict[str, Any]]] = {}

    # Registro de tentativas --------------------------------------------------------

    def _pending(
        self, connection, entity: str, sql: str, params: tuple = ()
    ) -> list[dict[str, Any]]:
        rows = [dict(row) for row in connection.execute(sql, params)]
        if not self.retry:
            tried = {
                int(row["entity_id"])
                for row in connection.execute(
                    "SELECT entity_id FROM image_attempts WHERE entity = ?", (entity,)
                )
            }
            rows = [row for row in rows if int(row["id"]) not in tried]
        return rows[: self.limit] if self.limit else rows

    @staticmethod
    def _mark(connection, entity: str, entity_id: int, found: bool) -> None:
        connection.execute(
            """
            INSERT INTO image_attempts (entity, entity_id, found) VALUES (?, ?, ?)
            ON CONFLICT(entity, entity_id) DO UPDATE SET
                found = excluded.found, tried_at = CURRENT_TIMESTAMP
            """,
            (entity, entity_id, int(found)),
        )

    def _save(
        self, connection, entity: str, entity_id: int, url: str, url_column: str, path_column: str
    ) -> bool:
        try:
            relative = self.downloader._download(url, entity, entity_id)
        except (ImageNotFound, ValueError):
            return False
        connection.execute(
            f"UPDATE {entity} SET {url_column} = ?, {path_column} = ? WHERE id = ?",
            (url, relative.as_posix(), entity_id),
        )
        return True

    def _run(
        self,
        entity: str,
        sql: str,
        find: Callable[[dict[str, Any]], str | None],
        url_column: str,
        path_column: str,
    ) -> dict[str, int]:
        report = {"baixadas": 0, "nao_encontradas": 0}
        with connect(self.database_path) as connection:
            rows = self._pending(connection, entity, sql)
        for row in rows:
            url = find(row)
            with connect(self.database_path) as connection:
                saved = bool(url) and self._save(
                    connection, entity, int(row["id"]), url, url_column, path_column
                )
                self._mark(connection, entity, int(row["id"]), saved)
            report["baixadas" if saved else "nao_encontradas"] += 1
            LOGGER.info("%s %s: %s", entity, row["name"], "ok" if saved else "não encontrado")
        return report

    # Ligas ----------------------------------------------------------------------------

    def leagues_of(self, country: str) -> list[dict[str, Any]]:
        if country not in self._leagues_by_country:
            payload = self.api.get("search_all_leagues.php", c=country, s="Soccer")
            self._leagues_by_country[country] = (
                payload.get("countries") or payload.get("leagues") or []
            )
        return self._leagues_by_country[country]

    def match_league(self, name: str, country: str | None) -> dict[str, Any] | None:
        wanted = _key(name)
        for country_name in _country_names(country):
            options = self.leagues_of(country_name)

            def names(record: dict[str, Any]) -> set[str]:
                alternates = str(record.get("strLeagueAlternate") or "").split(",")
                return {_key(record.get("strLeague"))} | {
                    _key(item) for item in alternates if item.strip()
                }

            exact = [record for record in options if wanted in names(record)]
            if len(exact) == 1:
                return exact[0]
            partial = [
                record
                for record in options
                if any(wanted in item or (item and item in wanted) for item in names(record))
            ]
            if len(partial) == 1:
                return partial[0]
        return None

    def collect_leagues(self) -> dict[str, int]:
        def find(row: dict[str, Any]) -> str | None:
            record = self.match_league(row["name"], row["country"])
            return _first_image(record, ("strBadge", "strLogo")) if record else None

        return self._run(
            "leagues",
            "SELECT id, name, country FROM leagues WHERE logo_path IS NULL ORDER BY id",
            find,
            "logo_url",
            "logo_path",
        )

    # Técnicos, estádios e lendas --------------------------------------------------------

    def _people(self, query: str) -> list[dict[str, Any]]:
        payload = self.api.get("searchplayers.php", p=query)
        return [
            record
            for record in payload.get("player") or []
            if str(record.get("strSport") or "Soccer").casefold() == "soccer"
        ]

    def match_person(
        self, names: list[str], nationality: str | None, *, prefer_manager: bool = False
    ) -> dict[str, Any] | None:
        countries = {_key(country) for country in _country_names(nationality)}
        for query in names:
            wanted = _key(query)
            found = [
                record for record in self._people(query) if _key(record.get("strPlayer")) == wanted
            ]
            if countries:
                same_country = [
                    record for record in found if _key(record.get("strNationality")) in countries
                ]
                found = same_country or ([] if len(found) > 1 else found)
            if prefer_manager and len(found) > 1:
                managers = [
                    record
                    for record in found
                    if any(
                        word in str(record.get("strPosition") or "").casefold()
                        for word in ("manager", "coach")
                    )
                ]
                found = managers or found
            if len(found) > 1:
                retired = [
                    record
                    for record in found
                    if str(record.get("strStatus") or "").casefold() == "retired"
                ]
                found = retired if len(retired) == 1 else found
            if len(found) == 1:
                return found[0]
        return None

    def collect_coaches(self) -> dict[str, int]:
        def find(row: dict[str, Any]) -> str | None:
            record = self.match_person([row["name"]], row["nationality"], prefer_manager=True)
            return _first_image(record, ("strCutout", "strThumb", "strRender")) if record else None

        return self._run(
            "coaches",
            "SELECT id, name, nationality FROM coaches WHERE photo_path IS NULL ORDER BY id",
            find,
            "photo_url",
            "photo_path",
        )

    def collect_stadiums(self) -> dict[str, int]:
        def find(row: dict[str, Any]) -> str | None:
            # O TheSportsDB costuma usar o nome oficial ("Estádio do Maracanã"): tenta com e
            # sem o prefixo e compara ignorando "Estádio/Stadium/do/de".
            wanted = _venue_key(row["name"])
            queries = [row["name"]]
            if not _key(row["name"]).startswith(("estadio", "stadium", "stade", "stadio")):
                queries += [f"Estadio do {row['name']}", f"Estadio {row['name']}"]
            for query in queries:
                payload = self.api.get("searchvenues.php", v=query)
                matches = [
                    record
                    for record in payload.get("venues") or []
                    if wanted
                    in {_venue_key(record.get("strVenue"))}
                    | {
                        _venue_key(item)
                        for item in str(record.get("strVenueAlternate") or "").split(",")
                    }
                ]
                if len(matches) == 1:
                    return _first_image(matches[0], ("strThumb", "strFanart1"))
            return None

        return self._run(
            "stadiums",
            "SELECT id, name FROM stadiums WHERE photo_path IS NULL ORDER BY id",
            find,
            "photo_url",
            "photo_path",
        )

    def legend_names(self, name: str) -> list[str]:
        """Nome, apelidos do arquivo das lendas e o nome sem o sufixo de apelido."""
        from src.encyclopedia import load_club_legends

        names = [name]
        for club in load_club_legends():
            for legend in club["players"]:
                if _key(legend["name"]) == _key(name):
                    names += legend.get("aliases", [])
        words = name.split()
        if len(words) > 1 and _key(words[-1]) in NICKNAME_SUFFIXES:
            names.append(" ".join(words[:-1]))
        return list(dict.fromkeys(names))

    def collect_legends(self) -> dict[str, int]:
        def find(row: dict[str, Any]) -> str | None:
            record = self.match_person(self.legend_names(row["name"]), row["nationality"])
            return _first_image(record, ("strCutout", "strThumb", "strRender")) if record else None

        marks = ", ".join("?" for _ in LEGEND_SOURCES)
        report = {"baixadas": 0, "nao_encontradas": 0}
        with connect(self.database_path) as connection:
            rows = self._pending(
                connection,
                "players",
                f"""
                SELECT id, name, nationality FROM players
                WHERE photo_path IS NULL AND overall_source IN ({marks}) ORDER BY id
                """,
                LEGEND_SOURCES,
            )
        for row in rows:
            url = find(row)
            with connect(self.database_path) as connection:
                saved = bool(url) and self._save(
                    connection, "players", int(row["id"]), url, "photo_url", "photo_path"
                )
                self._mark(connection, "players", int(row["id"]), saved)
            report["baixadas" if saved else "nao_encontradas"] += 1
            LOGGER.info("lenda %s: %s", row["name"], "ok" if saved else "não encontrada")
        return report

    # Times --------------------------------------------------------------------------

    def collect_teams(self) -> dict[str, int]:
        from src.game import _club_key

        report = {"escudos_novos": 0, "times_criados": 0, "ja_tinham": 0, "ligas_sem_times": 0}
        with connect(self.database_path) as connection:
            leagues = [
                dict(row)
                for row in connection.execute("SELECT id, name, country FROM leagues ORDER BY id")
            ]
            clubs: dict[str, dict[str, Any]] = {}
            for row in connection.execute("SELECT id, name, logo_path FROM clubs"):
                clubs.setdefault(_club_key(row["name"]), dict(row))
        if self.limit:
            leagues = leagues[: self.limit]
        for league in leagues:
            record = self.match_league(league["name"], league["country"])
            if record is None:
                report["ligas_sem_times"] += 1
                continue
            payload = self.api.get("search_all_teams.php", l=str(record.get("strLeague")))
            for team in payload.get("teams") or []:
                if str(team.get("strSport") or "Soccer").casefold() != "soccer":
                    continue
                names = [team.get("strTeam"), team.get("strTeamShort")]
                names += str(team.get("strTeamAlternate") or "").split(",")
                keys = [_club_key(name) for name in names if name and name.strip()]
                existing = next((clubs[key] for key in keys if key in clubs), None)
                badge = _first_image(team, ("strBadge", "strTeamBadge", "strLogo"))
                with connect(self.database_path) as connection:
                    if existing is not None:
                        if existing.get("logo_path") or not badge:
                            report["ja_tinham"] += 1
                            continue
                        if self._save(
                            connection, "clubs", int(existing["id"]), badge, "logo_url", "logo_path"
                        ):
                            existing["logo_path"] = "baixado"
                            report["escudos_novos"] += 1
                        continue
                    if not badge:
                        continue
                    club_id = connection.execute(
                        "INSERT INTO clubs (name, country, league_id, logo_url) VALUES (?, ?, ?, ?)",
                        (str(team["strTeam"]).strip(), team.get("strCountry"), league["id"], badge),
                    ).lastrowid
                    self._save(connection, "clubs", int(club_id), badge, "logo_url", "logo_path")
                clubs[keys[0]] = {"id": club_id, "name": team["strTeam"], "logo_path": "baixado"}
                for key in keys[1:]:
                    clubs.setdefault(key, clubs[keys[0]])
                report["times_criados"] += 1
                LOGGER.info("time novo: %s (%s)", team["strTeam"], league["name"])
        return report

    def run(self, types: Iterable[str]) -> dict[str, dict[str, int]]:
        steps = {
            "ligas": self.collect_leagues,
            "tecnicos": self.collect_coaches,
            "estadios": self.collect_stadiums,
            "lendas": self.collect_legends,
            "times": self.collect_teams,
        }
        return {kind: steps[kind]() for kind in types}


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--database", type=Path, default=DATABASE_PATH)
    parser.add_argument("--photo-root", type=Path, default=PHOTO_ROOT)
    parser.add_argument(
        "--tipos", default=",".join(TYPES), help=f"separados por vírgula: {', '.join(TYPES)}"
    )
    parser.add_argument("--limite", type=int, help="no máximo N registros por tipo (para testar)")
    parser.add_argument(
        "--retentar", action="store_true", help="busca de novo quem não foi encontrado antes"
    )
    parser.add_argument(
        "--api-key", help="chave do TheSportsDB; prefira THESPORTSDB_API_KEY no .env"
    )
    args = parser.parse_args()
    load_dotenv(ROOT / ".env")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    api_key = args.api_key or os.getenv("THESPORTSDB_API_KEY", "")
    if not api_key:
        parser.error(
            "Configure THESPORTSDB_API_KEY no .env (a chave gratuita é 123) ou use --api-key."
        )
    types = [item.strip() for item in args.tipos.split(",") if item.strip()]
    unknown = [item for item in types if item not in TYPES]
    if unknown:
        parser.error(f"Tipos desconhecidos: {', '.join(unknown)}. Use {', '.join(TYPES)}.")
    initialize_database(args.database)
    harvester = ImageHarvester(
        args.database, SportsDB(api_key), args.photo_root, retry=args.retentar, limit=args.limite
    )
    print(json.dumps(harvester.run(types), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
