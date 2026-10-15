"""Cliente API-Football com controle de cota e importação retomável."""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv

from src.db import DATABASE_PATH, ROOT, connect, initialize_database

BASE_URL = "https://v3.football.api-sports.io"
TIMEOUT = (5, 30)


class ApiFootballClient:
    def __init__(
        self,
        api_key: str | None = None,
        session: requests.Session | None = None,
    ):
        load_dotenv(ROOT / ".env")
        self._api_key = api_key or os.getenv("API_FOOTBALL_KEY")
        if not self._api_key:
            raise ValueError("Configure API_FOOTBALL_KEY no arquivo .env.")
        self._session = session or requests.Session()
        self.remaining_requests: int | None = None
        self.quota_date: str | None = None

    def players_page(
        self, league_id: int, season: int, page: int
    ) -> tuple[list[dict[str, Any]], int]:
        """Busca uma página e exige o cabeçalho de cota em cada resposta."""
        if self.remaining_requests is not None and self.remaining_requests <= 0:
            raise RuntimeError(
                "Cota diária API-Football esgotada; coleta interrompida."
            )
        if self.remaining_requests is not None:
            self.remaining_requests -= 1
            self.quota_date = datetime.now(timezone.utc).date().isoformat()
        try:
            response = self._session.get(
                f"{BASE_URL}/players",
                headers={"x-apisports-key": self._api_key},
                params={"league": league_id, "season": season, "page": page},
                timeout=TIMEOUT,
            )
        except requests.RequestException as error:
            raise RuntimeError("Falha de rede ao consultar API-Football.") from error

        remaining_header = response.headers.get("x-ratelimit-requests-remaining")
        if remaining_header is None:
            raise RuntimeError(
                "A resposta API-Football não informou "
                "x-ratelimit-requests-remaining; consulta interrompida."
            )
        try:
            self.remaining_requests = int(remaining_header)
        except ValueError as error:
            raise RuntimeError("Saldo de requisições API-Football inválido.") from error
        if self.remaining_requests < 0:
            raise RuntimeError("A API informou saldo negativo; consulta interrompida.")
        date_header = response.headers.get("Date")
        if date_header:
            try:
                self.quota_date = (
                    parsedate_to_datetime(date_header)
                    .astimezone(timezone.utc)
                    .date()
                    .isoformat()
                )
            except (TypeError, ValueError, OverflowError) as error:
                raise RuntimeError("Data inválida na resposta API-Football.") from error
        else:
            self.quota_date = datetime.now(timezone.utc).date().isoformat()
        if response.status_code >= 400:
            raise RuntimeError(
                f"API-Football respondeu HTTP {response.status_code}; "
                f"saldo informado: {self.remaining_requests}."
            )
        try:
            payload = response.json()
        except ValueError as error:
            raise RuntimeError("Resposta JSON inválida da API-Football.") from error
        errors = payload.get("errors")
        if errors:
            raise RuntimeError(f"Erro API-Football: {errors}")
        players = payload.get("response")
        if not isinstance(players, list):
            raise RuntimeError("Resposta API-Football sem lista de jogadores.")
        total_pages = int(payload.get("paging", {}).get("total", page))
        return players, total_pages


def _checkpoint_key(league_id: int, season: int) -> str:
    return f"api_football:league={league_id}:season={season}"


def _load_checkpoint(connection, source: str) -> dict[str, int]:
    row = connection.execute(
        "SELECT checkpoint FROM import_checkpoints WHERE source = ?", (source,)
    ).fetchone()
    if row is None:
        return {"page": 1, "offset": 0}
    try:
        checkpoint = json.loads(row["checkpoint"])
    except json.JSONDecodeError as error:
        raise RuntimeError(f"Checkpoint inválido para {source}.") from error
    page = checkpoint.get("page")
    offset = checkpoint.get("offset")
    if (
        not isinstance(page, int)
        or page < 1
        or not isinstance(offset, int)
        or offset < 0
    ):
        raise RuntimeError(f"Checkpoint inválido para {source}.")
    return {"page": page, "offset": offset}


def _save_checkpoint(connection, source: str, page: int, offset: int) -> None:
    connection.execute(
        """
        INSERT INTO import_checkpoints (source, checkpoint, updated_at)
        VALUES (?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(source) DO UPDATE SET
            checkpoint = excluded.checkpoint,
            updated_at = CURRENT_TIMESTAMP
        """,
        (source, json.dumps({"page": page, "offset": offset})),
    )


def _load_quota(connection, client: ApiFootballClient) -> None:
    today = datetime.now(timezone.utc).date().isoformat()
    row = connection.execute(
        "SELECT checkpoint FROM import_checkpoints WHERE source = ?",
        ("api_football:quota",),
    ).fetchone()
    if row is None:
        return
    try:
        saved = json.loads(row["checkpoint"])
    except json.JSONDecodeError as error:
        raise RuntimeError("Checkpoint inválido para a cota API-Football.") from error
    if saved.get("date") != today:
        return
    remaining = saved.get("remaining")
    if not isinstance(remaining, int) or remaining < 0:
        raise RuntimeError("Saldo salvo da API-Football inválido.")
    if client.remaining_requests is None:
        client.remaining_requests = remaining
    else:
        client.remaining_requests = min(client.remaining_requests, remaining)
    client.quota_date = today


def _save_quota(connection, client: ApiFootballClient) -> None:
    quota_date = getattr(client, "quota_date", None)
    if client.remaining_requests is None or quota_date is None:
        return
    connection.execute(
        """
        INSERT INTO import_checkpoints (source, checkpoint, updated_at)
        VALUES (?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(source) DO UPDATE SET
            checkpoint = excluded.checkpoint,
            updated_at = CURRENT_TIMESTAMP
        """,
        (
            json.dumps(
                {
                    "date": quota_date,
                    "remaining": client.remaining_requests,
                }
            ),
            "api_football:quota",
        ),
    )


def _upsert_team(connection, team: dict[str, Any], league_id: int) -> int | None:
    team_id = team.get("id")
    name = team.get("name")
    if team_id is None or not name:
        return None
    connection.execute(
        """
        INSERT INTO clubs (name, league_id, api_football_id, logo_url)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(api_football_id) DO UPDATE SET
            name = excluded.name,
            league_id = excluded.league_id,
            logo_url = COALESCE(excluded.logo_url, clubs.logo_url)
        """,
        (str(name), league_id, int(team_id), team.get("logo")),
    )
    return connection.execute(
        "SELECT id FROM clubs WHERE api_football_id = ?", (int(team_id),)
    ).fetchone()["id"]


def _upsert_api_player(
    connection,
    entry: dict[str, Any],
    league_id: int,
    season: int,
    is_current_season: bool,
) -> bool:
    player = entry.get("player") or {}
    player_source_id = player.get("id")
    name = player.get("name")
    if player_source_id is None or not name:
        return False
    statistics = entry.get("statistics") or []
    stat = statistics[0] if statistics else {}
    team = stat.get("team") or {}
    club_id = _upsert_team(connection, team, league_id)
    game_info = stat.get("games") or {}
    original_position = game_info.get("position")
    birth = player.get("birth") or {}
    birth_date = birth.get("date") if isinstance(birth, dict) else None
    nationality = player.get("nationality")
    photo = player.get("photo")
    existing = connection.execute(
        "SELECT player_id FROM player_sources WHERE source = ? AND source_id = ?",
        ("api_football", str(player_source_id)),
    ).fetchone()
    if existing:
        player_id = existing["player_id"]
        connection.execute(
            """
            UPDATE players SET
                name = ?, birth_date = ?, nationality = ?,
                club_id = CASE WHEN EXISTS (
                    SELECT 1 FROM player_sources
                    WHERE player_sources.player_id = players.id
                      AND player_sources.source = 'transfermarkt'
                ) THEN club_id ELSE ? END,
                original_position = ?, photo_url = ?,
                season_last_active = MAX(COALESCE(season_last_active, ?), ?),
                activity_status = CASE WHEN ? = 1
                    THEN 'active' ELSE activity_status END,
                phase2_candidate = CASE WHEN ? = 1
                    THEN 0 ELSE phase2_candidate END,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                str(name),
                birth_date,
                nationality,
                club_id,
                original_position,
                photo,
                season,
                season,
                int(is_current_season),
                int(is_current_season),
                player_id,
            ),
        )
        connection.execute(
            """
            UPDATE player_sources SET source_name = ?, source_club_name = ?,
                source_position = ?, source_season = ?
            WHERE source = ? AND source_id = ?
            """,
            (
                str(name),
                team.get("name"),
                original_position,
                season,
                "api_football",
                str(player_source_id),
            ),
        )
        return True

    player_id = connection.execute(
        """
        INSERT INTO players (
            name, birth_date, nationality, club_id, original_position, photo_url,
            season_last_active, activity_status, phase2_candidate
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            str(name),
            birth_date,
            nationality,
            club_id,
            original_position,
            photo,
            season,
            "active" if is_current_season else "unknown",
            0 if is_current_season else 1,
        ),
    ).lastrowid
    connection.execute(
        """
        INSERT INTO player_sources (
            source, source_id, player_id, source_name, source_club_name,
            source_position, source_season
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "api_football",
            str(player_source_id),
            player_id,
            str(name),
            team.get("name"),
            original_position,
            season,
        ),
    )
    return True


def import_league_players(
    client: ApiFootballClient,
    league_id: int,
    season: int,
    database_path: str | Path = DATABASE_PATH,
    limit: int | None = None,
    current_season: int | None = None,
) -> dict[str, int | None]:
    """Importa páginas em transações e salva posição de retomada."""
    if league_id < 1 or season < 1900:
        raise ValueError("Informe IDs de liga e temporada válidos.")
    if limit is not None and limit < 1:
        raise ValueError("--limit deve ser um inteiro maior que zero.")
    if current_season is None:
        current_season = datetime.now().year
    initialize_database(database_path)
    key = _checkpoint_key(league_id, season)
    league_api_id = league_id
    page_count = 0
    players_count = 0
    with connect(database_path) as connection:
        checkpoint = _load_checkpoint(connection, key)
        _load_quota(connection, client)
        page = checkpoint["page"]
        offset = checkpoint["offset"]

    while True:
        if client.remaining_requests is not None and client.remaining_requests <= 0:
            raise RuntimeError(
                "Cota diária API-Football esgotada; coleta interrompida."
            )
        try:
            entries, total_pages = client.players_page(league_id, season, page)
        finally:
            with connect(database_path) as connection:
                _save_quota(connection, client)
        if not entries:
            with connect(database_path) as connection:
                _save_checkpoint(connection, key, page + 1, 0)
            break

        start_offset = min(offset, len(entries))
        available = entries[start_offset:]
        if limit is not None:
            available = available[: limit - players_count]
        with connect(database_path) as connection:
            league_row = connection.execute(
                """
                SELECT id FROM leagues WHERE api_football_id = ?
                """,
                (league_api_id,),
            ).fetchone()
            if league_row:
                internal_league_id = league_row["id"]
            else:
                internal_league_id = connection.execute(
                    """
                    INSERT INTO leagues (name, api_football_id)
                    VALUES (?, ?)
                    """,
                    (f"API-Football {league_id}", league_api_id),
                ).lastrowid
            imported_in_page = 0
            for entry in available:
                if _upsert_api_player(
                    connection,
                    entry,
                    internal_league_id,
                    season,
                    season == current_season,
                ):
                    imported_in_page += 1
            players_count += imported_in_page
            page_count += 1

            rows_processed = start_offset + len(available)
            if (
                limit is not None
                and players_count >= limit
                and rows_processed < len(entries)
            ):
                _save_checkpoint(connection, key, page, rows_processed)
                return {
                    "players": players_count,
                    "pages": page_count,
                    "remaining": client.remaining_requests,
                }
            next_page = page + 1
            if rows_processed < len(entries):
                next_page = page
                next_offset = rows_processed
            else:
                next_offset = 0
            _save_checkpoint(connection, key, next_page, next_offset)

        if limit is not None and players_count >= limit:
            break
        if page >= total_pages:
            break
        page += 1
        offset = 0

    return {
        "players": players_count,
        "pages": page_count,
        "remaining": client.remaining_requests,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--league", type=int, required=True, help="ID da liga na API")
    parser.add_argument(
        "--season", type=int, required=True, help="temporada autorizada"
    )
    parser.add_argument("--database", type=Path, default=DATABASE_PATH)
    parser.add_argument(
        "--current-season",
        type=int,
        default=datetime.now().year,
        help="ano de início da temporada atual (padrão: ano corrente)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="limita jogadores para teste; use primeiro para validar a temporada",
    )
    args = parser.parse_args()
    try:
        client = ApiFootballClient()
        result = import_league_players(
            client,
            args.league,
            args.season,
            args.database,
            args.limit,
            current_season=args.current_season,
        )
    except (ValueError, RuntimeError) as error:
        parser.error(str(error))
    print(
        f"API-Football: {result['players']} jogadores em {result['pages']} páginas; "
        f"saldo diário informado: {result['remaining']}"
    )


if __name__ == "__main__":
    main()
