"""Pintos Corridos e Copa IOS: os campeonatos do servidor, jogados um dia de cada vez.

Cada **edição** junta as duas competições com os mesmos times:

- Entram sozinhos todos do servidor com o time completo (11 titulares válidos), até 16:
  primeiro quem ficou de fora na edição anterior, depois por ordem de criação do time.
  Os demais ficam na lista de espera e têm prioridade na próxima.
- **Pintos Corridos**: pontos corridos, só ida (todos contra todos uma vez), uma rodada por
  dia. Os dois primeiros fazem a final no dia seguinte à última rodada.
- **Copa IOS**: mata-mata sorteado, uma fase por dia a partir do dia 1 (oitavas, quartas,
  semi e final com 16 times). Com menos times, alguns passam direto na primeira fase.
  Empate no mata-mata (e na final do Pintos) vai para os pênaltis.
- Quem está sem escalação válida no dia do jogo perde por W.O. (3 × 0).

Terminada a final do Pintos, os prêmios são pagos e a próxima edição já fica criada; ela
começa no dia seguinte (`opens_on`), no horário dos jogos.
"""

from __future__ import annotations

import json
import math
import random
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from src.clock import BRASILIA
from src.db import DATABASE_PATH, connect
from src.income import add_box_office
from src.inventory import INVENTORY_ITEMS, give
from src.missions import record
from src.simulation import penalty_shootout_kicks, simulate_match
from src.teams import TeamError, get_valid_lineup

MAX_TEAMS = 16
MIN_TEAMS = 4
WALKOVER_GOALS = 3
# Fases da Copa pelo tamanho da chave.
COPA_STAGES = {
    16: ("oitavas", "quartas", "semi", "final"),
    8: ("quartas", "semi", "final"),
    4: ("semi", "final"),
}
STAGE_LABELS = {
    "oitavas": "Oitavas de final",
    "quartas": "Quartas de final",
    "semi": "Semifinal",
    "final": "Final",
}
# Prêmios: (Memilets, itens do inventário).
PINTOS_PRIZES = {
    1: (15_000, ("caixa_epica", "pacote_epico")),
    2: (8_000, ("caixa_epica",)),
    3: (5_000, ()),
    4: (3_000, ()),
}
PINTOS_OTHERS = 1_000
PINTOS_WIN_BONUS = 150  # por vitória na fase de pontos corridos
COPA_PRIZES = {
    "campeao": (8_000, ("caixa_epica",)),
    "vice": (4_000, ()),
    "semi": (2_000, ()),
    "quartas": (1_000, ()),
}
PLACE_LABELS = {
    "campeao": "🏆 Campeão",
    "vice": "🥈 Vice",
    "3": "🥉 3º lugar",
    "4": "4º lugar",
    "semi": "Semifinalista",
    "quartas": "Quartas de final",
    "participante": "Participante",
}


class LeagueError(Exception):
    """Regra do campeonato que pode ser mostrada para a pessoa."""


def today(now: datetime | None = None) -> date:
    value = now or datetime.now(timezone.utc)
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("now deve incluir um fuso horário.")
    return value.astimezone(BRASILIA).date()


# Edições -----------------------------------------------------------------------------------


def current_edition(guild_id: int | str, database_path: str | Path = DATABASE_PATH) -> dict | None:
    """A edição mais recente do servidor (em inscrição, em andamento ou a última encerrada)."""
    with connect(database_path) as connection:
        row = connection.execute(
            "SELECT * FROM league_editions WHERE guild_id = ? ORDER BY number DESC LIMIT 1",
            (str(guild_id),),
        ).fetchone()
    return dict(row) if row else None


def ensure_edition(
    guild_id: int | str, database_path: str | Path = DATABASE_PATH, *, now: datetime | None = None
) -> dict:
    """A edição atual; cria a próxima (em inscrição) se não houver ou se a última acabou."""
    edition = current_edition(guild_id, database_path)
    if edition and edition["status"] != "completed":
        return edition
    number = edition["number"] + 1 if edition else 1
    # Depois de uma final, a próxima começa no dia seguinte; a primeira já pode começar hoje.
    opens = today(now) + timedelta(days=1) if edition else today(now)
    with connect(database_path) as connection:
        connection.execute(
            """
            INSERT OR IGNORE INTO league_editions (guild_id, number, opens_on)
            VALUES (?, ?, ?)
            """,
            (str(guild_id), number, opens.isoformat()),
        )
    return current_edition(guild_id, database_path)


def eligible_users(database_path: str | Path = DATABASE_PATH) -> list[str]:
    """Quem tem o time completo (11 titulares válidos), por ordem de criação do time."""
    with connect(database_path) as connection:
        rows = connection.execute(
            "SELECT discord_user_id FROM game_teams ORDER BY created_at, discord_user_id"
        ).fetchall()
    ready = []
    for row in rows:
        try:
            get_valid_lineup(row["discord_user_id"], database_path)
        except (TeamError, LookupError):
            continue
        ready.append(str(row["discord_user_id"]))
    return ready


def _round_robin(teams: list[str]) -> list[list[tuple[str, str]]]:
    """Rodadas de todos contra todos (método do círculo, mando da tabela de Berger).

    Com número ímpar, um time folga por rodada. O mando fica equilibrado: no máximo um jogo
    em casa de diferença entre os times (dois, com número ímpar de times)."""
    pool: list[str | None] = list(teams) + ([None] if len(teams) % 2 else [])
    size = len(pool)
    rounds = []
    for number in range(size - 1):
        games = []
        for index in range(size // 2):
            first, second = pool[index], pool[size - 1 - index]
            # O time fixo alterna o mando a cada rodada; os outros pares, pela posição.
            flip = number % 2 if index == 0 else index % 2 == 0
            home, away = (second, first) if flip else (first, second)
            if home is not None and away is not None:
                games.append((home, away))
        rounds.append(games)
        pool = [pool[0], pool[-1], *pool[1:-1]]
    return rounds


def _copa_draw(teams: list[str], rng: random.Random) -> list[tuple[str, str | None]]:
    """Primeira fase da Copa: sorteio, com quem passa direto (adversário None) se faltar time."""
    size = 2 ** math.ceil(math.log2(len(teams)))
    shuffled = list(teams)
    rng.shuffle(shuffled)
    byes = size - len(teams)
    games: list[tuple[str, str | None]] = [(team, None) for team in shuffled[:byes]]
    rest = shuffled[byes:]
    games += [(rest[index], rest[index + 1]) for index in range(0, len(rest), 2)]
    rng.shuffle(games)
    return games


def start_edition(
    guild_id: int | str,
    eligible: list[str],
    database_path: str | Path = DATABASE_PATH,
    *,
    now: datetime | None = None,
    rng: random.Random | None = None,
    force: bool = False,
) -> dict:
    """Fecha as inscrições: monta os participantes, a tabela do Pintos e o sorteio da Copa.

    `eligible` são os ids (do servidor) com time completo, na ordem de `eligible_users`."""
    generator = rng or random.SystemRandom()
    edition = ensure_edition(guild_id, database_path, now=now)
    if edition["status"] != "registration":
        raise LeagueError("Essa edição já começou.")
    day_one = today(now)
    if not force and day_one < date.fromisoformat(edition["opens_on"]):
        raise LeagueError("A próxima edição começa amanhã.")
    with connect(database_path) as connection:
        previous = connection.execute(
            """
            SELECT e.discord_user_id FROM league_entries AS e
            JOIN league_editions AS d ON d.id = e.edition_id
            WHERE d.guild_id = ? AND d.number = ? AND e.waitlisted = 1
            ORDER BY e.seed
            """,
            (str(guild_id), edition["number"] - 1),
        ).fetchall()
        names = {
            row["discord_user_id"]: row["team_name"]
            for row in connection.execute("SELECT discord_user_id, team_name FROM game_teams")
        }
    ready = set(eligible)
    order = [row["discord_user_id"] for row in previous if row["discord_user_id"] in ready]
    order += [user for user in eligible if user not in order]
    if len(order) < MIN_TEAMS:
        raise LeagueError(
            f"São necessários pelo menos {MIN_TEAMS} times completos para começar "
            f"(há {len(order)})."
        )
    playing, waiting = order[:MAX_TEAMS], order[MAX_TEAMS:]
    rounds = _round_robin(playing)
    copa = _copa_draw(playing, generator)
    first_stage = COPA_STAGES[2 ** math.ceil(math.log2(len(playing)))][0]
    with connect(database_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        for seed, user in enumerate(order):
            connection.execute(
                """
                INSERT INTO league_entries (edition_id, discord_user_id, team_name, seed, waitlisted)
                VALUES (?, ?, ?, ?, ?)
                """,
                (edition["id"], user, names.get(user) or "Time", seed, int(user in waiting)),
            )
        for number, games in enumerate(rounds, start=1):
            for slot, (home, away) in enumerate(games):
                connection.execute(
                    """
                    INSERT INTO league_fixtures
                        (edition_id, competition, stage, day, slot, home_user_id, away_user_id)
                    VALUES (?, 'pintos', ?, ?, ?, ?, ?)
                    """,
                    (edition["id"], f"rodada {number}", number, slot, home, away),
                )
        played_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        for slot, (home, away) in enumerate(copa):
            bye = away is None
            connection.execute(
                """
                INSERT INTO league_fixtures (
                    edition_id, competition, stage, day, slot, home_user_id, away_user_id,
                    winner_user_id, walkover, played_at
                ) VALUES (?, 'copa', ?, 1, ?, ?, ?, ?, ?, ?)
                """,
                (
                    edition["id"],
                    first_stage,
                    slot,
                    home,
                    away,
                    home if bye else None,
                    "folga" if bye else None,
                    played_at if bye else None,
                ),
            )
        connection.execute(
            "UPDATE league_editions SET status = 'playing', start_date = ? WHERE id = ?",
            (day_one.isoformat(), edition["id"]),
        )
    return current_edition(guild_id, database_path)


def day_number(edition: dict, now: datetime | None = None) -> int:
    """Que dia da edição é hoje (o dia 1 é o do início)."""
    if not edition.get("start_date"):
        return 0
    return (today(now) - date.fromisoformat(edition["start_date"])).days + 1


# Consultas ---------------------------------------------------------------------------------


def fixtures(edition_id: int, database_path: str | Path = DATABASE_PATH) -> list[dict]:
    with connect(database_path) as connection:
        return [
            dict(row)
            for row in connection.execute(
                """
                SELECT * FROM league_fixtures WHERE edition_id = ?
                ORDER BY day, CASE competition WHEN 'copa' THEN 0 ELSE 1 END, slot
                """,
                (edition_id,),
            )
        ]


def due_fixtures(edition: dict, now: datetime | None = None, database_path=DATABASE_PATH):
    """Jogos que já deviam ter acontecido até hoje e ainda não foram jogados (Copa antes)."""
    day = day_number(edition, now)
    return [
        fixture
        for fixture in fixtures(edition["id"], database_path)
        if fixture["played_at"] is None and fixture["day"] <= day
    ]


def team_info(database_path: str | Path = DATABASE_PATH) -> dict[str, dict]:
    """Nome, cor e escudos atuais de cada time (para tabelas e imagens)."""
    with connect(database_path) as connection:
        return {row["discord_user_id"]: dict(row) for row in connection.execute("""
                SELECT discord_user_id, team_name, color, logo_path, custom_logo_path
                FROM game_teams
                """)}


def entries(edition_id: int, database_path: str | Path = DATABASE_PATH) -> list[dict]:
    with connect(database_path) as connection:
        return [
            dict(row)
            for row in connection.execute(
                "SELECT * FROM league_entries WHERE edition_id = ? ORDER BY seed", (edition_id,)
            )
        ]


def standings(edition_id: int, database_path: str | Path = DATABASE_PATH) -> list[dict]:
    """Tabela do Pintos Corridos: 3 pontos por vitória, 1 por empate.

    Desempate: pontos, vitórias, saldo, gols marcados e nome. W.O. dos dois lados conta
    como derrota para ambos."""
    info = team_info(database_path)
    table: dict[str, dict] = {}
    for entry in entries(edition_id, database_path):
        if entry["waitlisted"]:
            continue
        user = entry["discord_user_id"]
        team = info.get(user, {})
        table[user] = {
            "discord_user_id": user,
            "team_name": team.get("team_name") or entry["team_name"],
            "color": team.get("color") or "#2E8B57",
            "logo_path": team.get("logo_path"),
            "custom_logo_path": team.get("custom_logo_path"),
            "played": 0,
            "wins": 0,
            "draws": 0,
            "losses": 0,
            "gf": 0,
            "ga": 0,
            "points": 0,
            "form": [],
        }
    for fixture in fixtures(edition_id, database_path):
        if fixture["competition"] != "pintos" or fixture["stage"] == "final":
            continue
        if fixture["played_at"] is None:
            continue
        home, away = table.get(fixture["home_user_id"]), table.get(fixture["away_user_id"])
        if home is None or away is None:
            continue
        home_goals, away_goals = int(fixture["home_goals"]), int(fixture["away_goals"])
        for team, scored, conceded in (
            (home, home_goals, away_goals),
            (away, away_goals, home_goals),
        ):
            team["played"] += 1
            team["gf"] += scored
            team["ga"] += conceded
        if fixture["walkover"] == "ambos":
            outcomes = ("D", "D")
        elif home_goals > away_goals:
            outcomes = ("V", "D")
        elif away_goals > home_goals:
            outcomes = ("D", "V")
        else:
            outcomes = ("E", "E")
        for team, outcome in zip((home, away), outcomes):
            team["form"].append(outcome)
            if outcome == "V":
                team["wins"] += 1
                team["points"] += 3
            elif outcome == "E":
                team["draws"] += 1
                team["points"] += 1
            else:
                team["losses"] += 1
    return sorted(
        table.values(),
        key=lambda team: (
            -team["points"],
            -team["wins"],
            -(team["gf"] - team["ga"]),
            -team["gf"],
            team["team_name"].casefold(),
        ),
    )


def rounds_total(edition_id: int, database_path: str | Path = DATABASE_PATH) -> int:
    with connect(database_path) as connection:
        row = connection.execute(
            """
            SELECT MAX(day) FROM league_fixtures
            WHERE edition_id = ? AND competition = 'pintos' AND stage != 'final'
            """,
            (edition_id,),
        ).fetchone()
    return int(row[0] or 0)


# Jogos -------------------------------------------------------------------------------------


def _knockout(fixture: dict) -> bool:
    return fixture["competition"] == "copa" or fixture["stage"] == "final"


def play_fixture(
    fixture: dict, rng: random.Random | None = None, database_path: str | Path = DATABASE_PATH
) -> dict:
    """Joga uma partida (ou dá o W.O.) e grava o resultado. Devolve a partida atualizada."""
    generator = rng or random.SystemRandom()
    lineups = {}
    for side in ("home", "away"):
        try:
            lineups[side] = get_valid_lineup(fixture[f"{side}_user_id"], database_path)
        except (TeamError, LookupError):
            lineups[side] = None
    events: dict[str, Any] = {}
    penalties: tuple[int, int] | None = None
    walkover = None
    if lineups["home"] is None or lineups["away"] is None:
        # W.O.: quem tem time vence por 3 × 0; sem nenhum dos dois, derrota para ambos.
        if lineups["home"] is None and lineups["away"] is None:
            walkover, home_goals, away_goals = "ambos", 0, 0
            winner = fixture["home_user_id"] if _knockout(fixture) else None
        elif lineups["home"] is None:
            walkover, home_goals, away_goals = "mandante", 0, WALKOVER_GOALS
            winner = fixture["away_user_id"]
        else:
            walkover, home_goals, away_goals = "visitante", WALKOVER_GOALS, 0
            winner = fixture["home_user_id"]
        events["walkover"] = walkover
    else:
        (home_team, home_players), (away_team, away_players) = lineups["home"], lineups["away"]
        result = simulate_match(
            home_team["team_name"],
            home_players,
            away_team["team_name"],
            away_players,
            home_style=home_team["style"],
            away_style=away_team["style"],
            home_tactics=home_team,
            away_tactics=away_team,
            home_advantage=True,
            rng=generator,
        )
        home_goals, away_goals = result.home_goals, result.away_goals
        events = result.to_dict()
        events["home_lineup"] = [int(player["id"]) for player in home_players]
        events["away_lineup"] = [int(player["id"]) for player in away_players]
        winner = None
        if home_goals != away_goals:
            winner = fixture["home_user_id"] if home_goals > away_goals else fixture["away_user_id"]
        elif _knockout(fixture):
            kicks = penalty_shootout_kicks(
                home_players,
                away_players,
                home_taker_id=home_team.get("penalty_player_id"),
                away_taker_id=away_team.get("penalty_player_id"),
                rng=generator,
            )
            penalties = (kicks[-1].home_score, kicks[-1].away_score)
            events["penalty_kicks"] = [asdict(kick) for kick in kicks]
            winner = (
                fixture["home_user_id"] if penalties[0] > penalties[1] else fixture["away_user_id"]
            )
    with connect(database_path) as connection:
        if walkover is None:
            # Andar 5 da torre: cada jogo oficial enche a bilheteria dos dois times.
            events["box_office"] = {
                side: add_box_office(
                    connection,
                    str(fixture[f"{side}_user_id"]),
                    winner == fixture[f"{side}_user_id"],
                )
                for side in ("home", "away")
            }
            for side in ("home", "away"):
                record(connection, str(fixture[f"{side}_user_id"]), "championship_match")
        connection.execute(
            """
            UPDATE league_fixtures
            SET home_goals = ?, away_goals = ?, home_penalties = ?, away_penalties = ?,
                winner_user_id = ?, walkover = ?, events_json = ?, played_at = ?
            WHERE id = ? AND played_at IS NULL
            """,
            (
                home_goals,
                away_goals,
                penalties[0] if penalties else None,
                penalties[1] if penalties else None,
                winner,
                walkover,
                json.dumps(events, ensure_ascii=False),
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
                fixture["id"],
            ),
        )
        row = connection.execute(
            "SELECT * FROM league_fixtures WHERE id = ?", (fixture["id"],)
        ).fetchone()
    return dict(row)


def advance(
    edition: dict, database_path: str | Path = DATABASE_PATH, *, now: datetime | None = None
) -> dict[str, Any]:
    """Depois dos jogos: monta a próxima fase da Copa, a final do Pintos e paga os prêmios.

    Devolve o que aconteceu: {"copa_champion", "pintos_champion", "prizes", "next_edition"}."""
    news: dict[str, Any] = {"prizes": {}}
    games = fixtures(edition["id"], database_path)
    copa = [game for game in games if game["competition"] == "copa"]
    stages = [
        stage for stage in COPA_STAGES[_copa_size(copa)] if any(g["stage"] == stage for g in copa)
    ]
    last = stages[-1] if stages else None
    current = [game for game in copa if game["stage"] == last]
    if current and all(game["played_at"] for game in current):
        if last == "final":
            if not _paid(edition["id"], "copa", database_path):
                news["prizes"]["copa"] = _pay_copa(edition["id"], copa, database_path)
                news["copa_champion"] = current[0]["winner_user_id"]
        else:
            following = COPA_STAGES[_copa_size(copa)][len(stages)]
            winners = [game["winner_user_id"] for game in sorted(current, key=lambda g: g["slot"])]
            day = current[0]["day"] + 1
            with connect(database_path) as connection:
                for slot in range(len(winners) // 2):
                    connection.execute(
                        """
                        INSERT OR IGNORE INTO league_fixtures (
                            edition_id, competition, stage, day, slot, home_user_id, away_user_id
                        ) VALUES (?, 'copa', ?, ?, ?, ?, ?)
                        """,
                        (
                            edition["id"],
                            following,
                            day,
                            slot,
                            winners[2 * slot],
                            winners[2 * slot + 1],
                        ),
                    )
    pintos = [game for game in games if game["competition"] == "pintos"]
    regular = [game for game in pintos if game["stage"] != "final"]
    final = next((game for game in pintos if game["stage"] == "final"), None)
    if regular and all(game["played_at"] for game in regular) and final is None:
        top = standings(edition["id"], database_path)[:2]
        with connect(database_path) as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO league_fixtures (
                    edition_id, competition, stage, day, slot, home_user_id, away_user_id
                ) VALUES (?, 'pintos', 'final', ?, 0, ?, ?)
                """,
                (
                    edition["id"],
                    max(game["day"] for game in regular) + 1,
                    top[0]["discord_user_id"],
                    top[1]["discord_user_id"],
                ),
            )
    if (
        final is not None
        and final["played_at"]
        and not _paid(edition["id"], "pintos", database_path)
    ):
        news["prizes"]["pintos"] = _pay_pintos(edition["id"], final, database_path)
        news["pintos_champion"] = final["winner_user_id"]
        with connect(database_path) as connection:
            connection.execute(
                "UPDATE league_editions SET status = 'completed', completed_at = ? WHERE id = ?",
                (datetime.now(timezone.utc).isoformat(timespec="seconds"), edition["id"]),
            )
        news["next_edition"] = ensure_edition(edition["guild_id"], database_path, now=now)
    return news


def _copa_size(copa: list[dict]) -> int:
    """Tamanho da chave: a primeira fase (dia 1) tem metade dele em jogos."""
    first = sum(1 for game in copa if game["day"] == 1)
    return 2 * first if first else 4


# Prêmios -----------------------------------------------------------------------------------


def _paid(edition_id: int, competition: str, database_path) -> bool:
    with connect(database_path) as connection:
        return bool(
            connection.execute(
                "SELECT 1 FROM league_prizes WHERE edition_id = ? AND competition = ? LIMIT 1",
                (edition_id, competition),
            ).fetchone()
        )


def _pay(
    edition_id: int, competition: str, awards: list[tuple[str, str, int, tuple]], database_path
):
    """Paga e registra os prêmios: (usuário, colocação, Memilets, itens)."""
    with connect(database_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        for user, place, memilets, items in awards:
            connection.execute(
                """
                INSERT INTO league_prizes
                    (edition_id, competition, discord_user_id, place, memilets, items)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (edition_id, competition, user, place, memilets, ",".join(items)),
            )
            connection.execute(
                "UPDATE game_users SET memilets = memilets + ?, updated_at = CURRENT_TIMESTAMP "
                "WHERE discord_user_id = ?",
                (memilets, user),
            )
            for item in items:
                give(connection, user, item)
    return [
        {"user_id": user, "place": place, "memilets": memilets, "items": list(items)}
        for user, place, memilets, items in awards
    ]


def _pay_pintos(edition_id: int, final: dict, database_path) -> list[dict]:
    table = standings(edition_id, database_path)
    champion = final["winner_user_id"]
    runner_up = (
        final["away_user_id"] if champion == final["home_user_id"] else final["home_user_id"]
    )
    awards = []
    for position, team in enumerate(table, start=1):
        user = team["discord_user_id"]
        if user == champion:
            place, rank = "campeao", 1
        elif user == runner_up:
            place, rank = "vice", 2
        else:
            rank = position
            place = str(position) if position in (3, 4) else "participante"
        memilets, items = PINTOS_PRIZES.get(rank, (PINTOS_OTHERS, ()))
        awards.append((user, place, memilets + PINTOS_WIN_BONUS * team["wins"], items))
    return _pay(edition_id, "pintos", awards, database_path)


def _pay_copa(edition_id: int, copa: list[dict], database_path) -> list[dict]:
    awards = []
    for game in copa:
        if game["walkover"] == "folga" or game["stage"] not in ("final", "semi", "quartas"):
            continue
        loser = (
            game["away_user_id"]
            if game["winner_user_id"] == game["home_user_id"]
            else game["home_user_id"]
        )
        if game["stage"] == "final":
            awards.append((game["winner_user_id"], "campeao", *COPA_PRIZES["campeao"]))
            awards.append((loser, "vice", *COPA_PRIZES["vice"]))
        else:
            awards.append((loser, game["stage"], *COPA_PRIZES[game["stage"]]))
    return _pay(edition_id, "copa", awards, database_path)


def prize_text(prize: dict) -> str:
    items = " + ".join(
        f"{INVENTORY_ITEMS[item][0]} {INVENTORY_ITEMS[item][1]}" for item in prize["items"]
    )
    return f"{prize['memilets']:,}".replace(",", ".") + (f" + {items}" if items else "")


def history(guild_id: int | str, database_path: str | Path = DATABASE_PATH) -> list[dict]:
    """Edições encerradas, da mais recente para a mais antiga, com os campeões."""
    info = team_info(database_path)
    with connect(database_path) as connection:
        editions = [
            dict(row)
            for row in connection.execute(
                """
                SELECT * FROM league_editions WHERE guild_id = ? AND status = 'completed'
                ORDER BY number DESC
                """,
                (str(guild_id),),
            )
        ]
        for edition in editions:
            for row in connection.execute(
                """
                SELECT competition, discord_user_id, place FROM league_prizes
                WHERE edition_id = ? AND place IN ('campeao', 'vice')
                """,
                (edition["id"],),
            ):
                name = (info.get(row["discord_user_id"]) or {}).get("team_name") or "Time"
                edition[f"{row['competition']}_{row['place']}"] = name
    return editions
