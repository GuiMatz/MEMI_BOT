"""Pintos Corridos e Copa IOS: os campeonatos do servidor, jogados um dia de cada vez.

Cada **edição** junta as duas competições com os mesmos times, uma depois da outra:

- Entram sozinhos todos do servidor com o time completo (11 titulares válidos), até 16:
  primeiro quem ficou de fora na edição anterior, depois por ordem de criação do time.
  Os demais ficam na lista de espera e têm prioridade na próxima.
- **Pintos Corridos**: pontos corridos, só ida (todos contra todos uma vez), uma rodada por
  dia. Os dois primeiros fazem a final no dia seguinte à última rodada.
- **Copa IOS**: começa no dia seguinte à final do Pintos. Mata-mata, uma fase por dia
  (oitavas, quartas, semi e final com 16 times). O campeão e o vice do Pintos ficam em
  lados opostos da chave (só se cruzam na final); o resto é sorteado. Com menos de 16,
  os mais bem colocados no Pintos passam direto na primeira fase. Empate no mata-mata (e
  na final do Pintos) vai para os pênaltis.
- Quem está sem escalação válida no dia do jogo perde por W.O. (3 × 0).

Cada competição paga os prêmios ao terminar. Depois da final da Copa a próxima edição já
fica criada; ela começa no dia seguinte (`opens_on`), no horário dos jogos.
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


def _copa_draw(seeded: list[str], rng: random.Random) -> list[tuple[str, str | None]]:
    """Primeira fase da Copa, em ordem de vaga na chave (vagas 2k e 2k+1 se cruzam depois).

    `seeded` vem na ordem do Pintos (campeão, vice, 3º...). O campeão fica na metade de cima
    e o vice na de baixo: só se encontram na final. Se faltar time para a chave cheia, quem
    passa direto (adversário None) são os mais bem colocados, alternando as metades; os
    outros jogos são sorteados."""
    size = 2 ** math.ceil(math.log2(len(seeded)))
    games = size // 2
    halves = (list(range(games // 2)), list(range(games // 2, games)))
    for half in halves:
        rng.shuffle(half)
    pairs: list[list[str | None]] = [[] for _ in range(games)]
    byes = size - len(seeded)
    # Cabeças de chave: campeão e vice sempre; e todos os que passam direto.
    heads = seeded[: max(2, byes)]
    for index, team in enumerate(heads):
        slot = halves[index % 2].pop()
        pairs[slot] = [team, None] if index < byes else [team]
    rest = seeded[len(heads) :]
    rng.shuffle(rest)
    free = [slot for slot in range(games) for _ in range(2 - len(pairs[slot]))]
    rng.shuffle(free)
    for team, slot in zip(rest, free):
        pairs[slot].append(team)
    return [(pair[0], pair[1]) for pair in pairs]


def _insert_copa(connection, edition_id: int, seeded: list[str], day: int, rng) -> None:
    """Grava a primeira fase da Copa; quem passa direto já sai com a vaga garantida."""
    stage = COPA_STAGES[2 ** math.ceil(math.log2(len(seeded)))][0]
    played_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for slot, (home, away) in enumerate(_copa_draw(seeded, rng)):
        bye = away is None
        connection.execute(
            """
            INSERT OR IGNORE INTO league_fixtures (
                edition_id, competition, stage, day, slot, home_user_id, away_user_id,
                winner_user_id, walkover, played_at
            ) VALUES (?, 'copa', ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                edition_id,
                stage,
                day,
                slot,
                home,
                away,
                home if bye else None,
                "folga" if bye else None,
                played_at if bye else None,
            ),
        )


def start_edition(
    guild_id: int | str,
    eligible: list[str],
    database_path: str | Path = DATABASE_PATH,
    *,
    now: datetime | None = None,
    force: bool = False,
) -> dict:
    """Fecha as inscrições: monta os participantes e a tabela do Pintos Corridos.

    `eligible` são os ids (do servidor) com time completo, na ordem de `eligible_users`."""
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


def pull_forward(
    edition: dict,
    database_path: str | Path = DATABASE_PATH,
    *,
    now: datetime | None = None,
    competition: str | None = None,
) -> dict:
    """Traz o próximo dia de jogos para hoje: o calendário inteiro anda um dia para trás.

    Assim a rodada seguinte cai hoje e as outras continuam uma por dia, sem dia vazio. Só
    adianta se hoje já não tiver jogo pendente. Com `competition` ("pintos" ou "copa"), recusa
    se a próxima coisa a jogar for da outra competição."""
    if edition["status"] != "playing":
        return edition
    games = [g for g in fixtures(edition["id"], database_path) if g["played_at"] is None]
    if competition:
        # Antes de tudo: o pedido é da competição certa (a Copa só vem depois do Pintos).
        if not any(g["competition"] == competition for g in games):
            raise LeagueError(_no_games_message(competition, edition, database_path))
        games = [g for g in games if g["competition"] == competition]
    if due_fixtures(edition, now, database_path):
        return edition
    pending = [g["day"] for g in games]
    if not pending:
        return edition
    shift = min(pending) - day_number(edition, now)
    start = date.fromisoformat(edition["start_date"]) - timedelta(days=shift)
    with connect(database_path) as connection:
        connection.execute(
            "UPDATE league_editions SET start_date = ? WHERE id = ?",
            (start.isoformat(), edition["id"]),
        )
    return {**edition, "start_date": start.isoformat()}


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
    with connect(database_path) as connection:
        removed = {
            row["discord_user_id"]
            for row in connection.execute(
                "SELECT discord_user_id FROM league_entries WHERE edition_id = ? AND removed = 1",
                (fixture["edition_id"],),
            )
        }
    lineups = {}
    for side in ("home", "away"):
        if fixture[f"{side}_user_id"] in removed:
            lineups[side] = None  # tirado da edição por um admin: perde por W.O.
            continue
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
    edition: dict,
    database_path: str | Path = DATABASE_PATH,
    *,
    now: datetime | None = None,
    rng: random.Random | None = None,
) -> dict[str, Any]:
    """Depois dos jogos do dia, monta o que vem a seguir e paga os prêmios.

    Ordem da edição: rodadas do Pintos, final do Pintos (os 2 primeiros), Copa IOS (uma
    fase por dia, sorteada com o campeão e o vice do Pintos em lados opostos). Acabada a
    final da Copa, a edição termina e a próxima começa no dia seguinte.

    Devolve o que aconteceu: {"prizes", "pintos_champion", "copa_draw", "copa_champion",
    "next_edition"}."""
    generator = rng or random.SystemRandom()
    news: dict[str, Any] = {"prizes": {}}
    games = fixtures(edition["id"], database_path)
    pintos = [game for game in games if game["competition"] == "pintos"]
    regular = [game for game in pintos if game["stage"] != "final"]
    final = next((game for game in pintos if game["stage"] == "final"), None)
    copa = [game for game in games if game["competition"] == "copa"]
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
    pintos_done = final is not None and final["played_at"]
    if pintos_done and not _paid(edition["id"], "pintos", database_path):
        news["prizes"]["pintos"] = _pay_pintos(edition["id"], final, database_path)
        champion = final["winner_user_id"]
        news["pintos_champion"] = champion
        seeded = _copa_seeds(edition["id"], final, database_path)
        with connect(database_path) as connection:
            _insert_copa(connection, edition["id"], seeded, final["day"] + 1, generator)
        news["copa_draw"] = True
        copa = [g for g in fixtures(edition["id"], database_path) if g["competition"] == "copa"]
    stages = [
        stage for stage in COPA_STAGES[_copa_size(copa)] if any(g["stage"] == stage for g in copa)
    ]
    last = stages[-1] if stages else None
    current = [game for game in copa if game["stage"] == last]
    if current and all(game["played_at"] for game in current):
        if last != "final":
            following = COPA_STAGES[_copa_size(copa)][len(stages)]
            winners = [game["winner_user_id"] for game in sorted(current, key=lambda g: g["slot"])]
            day = max(game["day"] for game in current) + 1
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
        elif not _paid(edition["id"], "copa", database_path):
            news["prizes"]["copa"] = _pay_copa(edition["id"], copa, database_path)
            news["copa_champion"] = current[0]["winner_user_id"]
            with connect(database_path) as connection:
                connection.execute(
                    "UPDATE league_editions SET status = 'completed', completed_at = ? WHERE id = ?",
                    (datetime.now(timezone.utc).isoformat(timespec="seconds"), edition["id"]),
                )
            news["next_edition"] = ensure_edition(edition["guild_id"], database_path, now=now)
    return news


def _copa_size(copa: list[dict]) -> int:
    """Tamanho da chave: a primeira fase (o primeiro dia de Copa) tem metade dele em jogos."""
    if not copa:
        return 4
    first_day = min(game["day"] for game in copa)
    return 2 * sum(1 for game in copa if game["day"] == first_day)


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


def prize_plain(prize: dict) -> str:
    """O prêmio sem emojis, para as imagens ("15.900 Memilets · caixa épica")."""
    parts = [f"{prize['memilets']:,} Memilets".replace(",", ".")]
    parts += [INVENTORY_ITEMS[item][1].lower() for item in prize["items"]]
    return " · ".join(parts)


def history(guild_id: int | str, database_path: str | Path = DATABASE_PATH) -> list[dict]:
    """Edições encerradas, da mais recente para a mais antiga, com os campeões."""
    info = team_info(database_path)
    with connect(database_path) as connection:
        editions = [
            dict(row)
            for row in connection.execute(
                """
                SELECT * FROM league_editions
                WHERE guild_id = ? AND status = 'completed' AND cancelled = 0
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


def bracket(edition_id: int, database_path: str | Path = DATABASE_PATH) -> list[dict]:
    """A chave inteira da Copa, fase por fase, já com as fases que ainda não têm times.

    Cada fase: {"stage", "day", "games"}; cada jogo é a partida gravada (ou None, se ainda
    não foi montada). O jogo `slot` de uma fase sai dos jogos 2·slot e 2·slot+1 da anterior,
    então a primeira metade de cada fase é o lado A da chave e a segunda, o lado B."""
    copa = [game for game in fixtures(edition_id, database_path) if game["competition"] == "copa"]
    if not copa:
        return []
    size = _copa_size(copa)
    first_day = min(game["day"] for game in copa)
    rounds = []
    for index, stage in enumerate(COPA_STAGES[size]):
        games: list[dict | None] = [None] * (size // 2 ** (index + 1))
        for game in copa:
            if game["stage"] == stage:
                games[game["slot"]] = game
        rounds.append({"stage": stage, "day": first_day + index, "games": games})
    return rounds


# Controles de admin ---------------------------------------------------------------------------


def shift_calendar(edition: dict, days: int, database_path: str | Path = DATABASE_PATH) -> dict:
    """Empurra o calendário da edição `days` dias para frente (ao retomar depois de uma pausa,
    os dias parados não viram jogos acumulados)."""
    if not edition.get("start_date") or not days:
        return edition
    start = date.fromisoformat(edition["start_date"]) + timedelta(days=days)
    with connect(database_path) as connection:
        connection.execute(
            "UPDATE league_editions SET start_date = ? WHERE id = ?",
            (start.isoformat(), edition["id"]),
        )
    return {**edition, "start_date": start.isoformat()}


def remove_team(edition: dict, user_id: int | str, database_path=DATABASE_PATH) -> int:
    """Tira um time da edição: os jogos que faltam dele viram W.O. (3 × 0 para o adversário).

    Devolve quantos jogos ele ainda tinha."""
    user = str(user_id)
    with connect(database_path) as connection:
        entry = connection.execute(
            """
            SELECT removed FROM league_entries
            WHERE edition_id = ? AND discord_user_id = ? AND waitlisted = 0
            """,
            (edition["id"], user),
        ).fetchone()
        if entry is None:
            raise LeagueError("Esse time não está jogando esta edição.")
        if entry["removed"]:
            raise LeagueError("Esse time já foi tirado desta edição.")
        connection.execute(
            "UPDATE league_entries SET removed = 1 WHERE edition_id = ? AND discord_user_id = ?",
            (edition["id"], user),
        )
        pending = connection.execute(
            """
            SELECT COUNT(*) FROM league_fixtures
            WHERE edition_id = ? AND played_at IS NULL
              AND (home_user_id = ? OR away_user_id = ?)
            """,
            (edition["id"], user, user),
        ).fetchone()[0]
    return int(pending)


def cancel_edition(
    edition: dict, database_path: str | Path = DATABASE_PATH, *, now: datetime | None = None
) -> dict:
    """Encerra a edição sem prêmios e abre a próxima, que já pode começar hoje.

    Os jogos já feitos continuam valendo no `mm!stats` e nas rivalidades."""
    if edition["status"] == "completed":
        raise LeagueError("Essa edição já terminou.")
    with connect(database_path) as connection:
        connection.execute(
            """
            UPDATE league_editions SET status = 'completed', cancelled = 1, completed_at = ?
            WHERE id = ?
            """,
            (datetime.now(timezone.utc).isoformat(timespec="seconds"), edition["id"]),
        )
        connection.execute(
            "INSERT OR IGNORE INTO league_editions (guild_id, number, opens_on) VALUES (?, ?, ?)",
            (edition["guild_id"], edition["number"] + 1, today(now).isoformat()),
        )
    return current_edition(edition["guild_id"], database_path)


def lineup_problems(edition: dict, database_path: str | Path = DATABASE_PATH) -> list[str]:
    """Times da edição que perderiam por W.O. hoje (sem escalação válida ou tirados)."""
    problems = []
    for entry in entries(edition["id"], database_path):
        if entry["waitlisted"]:
            continue
        if entry["removed"]:
            problems.append(entry["discord_user_id"])
            continue
        try:
            get_valid_lineup(entry["discord_user_id"], database_path)
        except (TeamError, LookupError):
            problems.append(entry["discord_user_id"])
    return problems


# Gerência por competição (`mm!pintos` e `mm!copa`) -------------------------------------------


def _no_games_message(competition: str, edition: dict, database_path) -> str:
    games = fixtures(edition["id"], database_path)
    if competition == "copa":
        if any(g["competition"] == "copa" for g in games):
            return "A Copa IOS desta edição já terminou."
        return "A Copa IOS só começa depois da final do Pintos Corridos."
    return "O Pintos Corridos desta edição já terminou; agora é a vez da Copa IOS."


def _copa_seeds(edition_id: int, final: dict, database_path) -> list[str]:
    """Ordem do Pintos para a Copa: campeão, vice e o resto pela tabela."""
    champion = final["winner_user_id"]
    vice = final["away_user_id"] if champion == final["home_user_id"] else final["home_user_id"]
    table = [team["discord_user_id"] for team in standings(edition_id, database_path)]
    return [champion, vice] + [user for user in table if user not in (champion, vice)]


def round_games(edition_id: int, number: int, database_path=DATABASE_PATH) -> list[dict]:
    """Os jogos de uma rodada do Pintos (a final é a rodada `rounds_total + 1`)."""
    stage = f"rodada {number}"
    games = [
        g
        for g in fixtures(edition_id, database_path)
        if g["competition"] == "pintos" and g["stage"] == stage
    ]
    if not games and number == rounds_total(edition_id, database_path) + 1:
        games = [
            g
            for g in fixtures(edition_id, database_path)
            if g["competition"] == "pintos" and g["stage"] == "final"
        ]
    return games


def current_round(edition_id: int, database_path=DATABASE_PATH) -> int:
    """A última rodada do Pintos já jogada (0 se nenhuma)."""
    played = [
        int(g["stage"].split()[1])
        for g in fixtures(edition_id, database_path)
        if g["competition"] == "pintos" and g["stage"] != "final" and g["played_at"]
    ]
    return max(played, default=0)


def end_pintos(
    edition: dict, database_path: str | Path = DATABASE_PATH, *, now: datetime | None = None
) -> dict:
    """Encerra a fase de pontos corridos agora: as rodadas que faltam somem (a tabela fica
    como está) e a final entre os 2 primeiros vai para o próximo dia de jogos.

    Devolve a final criada."""
    if edition["status"] != "playing":
        raise LeagueError("Não há edição em andamento.")
    games = [g for g in fixtures(edition["id"], database_path) if g["competition"] == "pintos"]
    if any(g["stage"] == "final" for g in games):
        raise LeagueError("A fase de pontos corridos já acabou: a final já está marcada.")
    played = [g for g in games if g["played_at"]]
    if not played:
        raise LeagueError("Jogue pelo menos uma rodada antes de encerrar o Pintos.")
    unplayed = [g["id"] for g in games if g["played_at"] is None]
    marks = ", ".join("?" * len(unplayed))
    with connect(database_path) as connection:
        if unplayed:
            connection.execute(f"DELETE FROM league_fixtures WHERE id IN ({marks})", unplayed)
    advance(edition, database_path, now=now)  # cria a final (dia seguinte à última rodada)
    final = next(
        g
        for g in fixtures(edition["id"], database_path)
        if g["competition"] == "pintos" and g["stage"] == "final"
    )
    # A final é no próximo dia de jogos: se o dia dela já passou, vem para hoje.
    day = max(final["day"], day_number(edition, now))
    if day != final["day"]:
        with connect(database_path) as connection:
            connection.execute(
                "UPDATE league_fixtures SET day = ? WHERE id = ?", (day, final["id"])
            )
        final = {**final, "day": day}
    return final


def replayable(edition: dict, user_id: int | str, database_path=DATABASE_PATH, *, now=None):
    """O jogo de hoje da pessoa que ainda dá para refazer, ou o motivo de não dar.

    Só jogos do dia; não dá para refazer final (já pagou prêmio) nem jogo da Copa cuja fase
    seguinte já começou."""
    user, day = str(user_id), day_number(edition, now)
    games = fixtures(edition["id"], database_path)
    mine = [
        g
        for g in games
        if g["played_at"]
        and g["day"] == day
        and g["walkover"] != "folga"
        and user in (g["home_user_id"], g["away_user_id"])
    ]
    if not mine:
        raise LeagueError("Essa pessoa não jogou nenhum jogo do campeonato hoje.")
    game = mine[-1]
    if game["stage"] == "final":
        raise LeagueError("Final não dá para refazer: os prêmios já foram pagos.")
    if game["competition"] == "copa":
        nxt = _next_copa_game(game, games)
        if nxt is not None and nxt["played_at"]:
            raise LeagueError("A fase seguinte da Copa já foi jogada; não dá para refazer.")
    return game


def _next_copa_game(game: dict, games: list[dict]) -> dict | None:
    stages = list(COPA_STAGES[_copa_size([g for g in games if g["competition"] == "copa"])])
    index = stages.index(game["stage"])
    if index + 1 >= len(stages):
        return None
    return next(
        (
            g
            for g in games
            if g["competition"] == "copa"
            and g["stage"] == stages[index + 1]
            and g["slot"] == game["slot"] // 2
        ),
        None,
    )


def reset_fixture(game: dict, database_path=DATABASE_PATH) -> dict:
    """Desfaz um jogo (placar, bilheteria) para ele ser jogado de novo."""
    events = json.loads(game["events_json"] or "{}")
    with connect(database_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        for side, amount in (events.get("box_office") or {}).items():
            if amount:
                connection.execute(
                    "UPDATE game_users SET box_office = MAX(0, box_office - ?) "
                    "WHERE discord_user_id = ?",
                    (int(amount), game[f"{side}_user_id"]),
                )
        connection.execute(
            """
            UPDATE league_fixtures
            SET home_goals = NULL, away_goals = NULL, home_penalties = NULL,
                away_penalties = NULL, winner_user_id = NULL, walkover = NULL,
                events_json = NULL, played_at = NULL
            WHERE id = ?
            """,
            (game["id"],),
        )
        row = connection.execute("SELECT * FROM league_fixtures WHERE id = ?", (game["id"],))
        return dict(row.fetchone())


def sync_copa_winner(game: dict, database_path=DATABASE_PATH) -> None:
    """Depois de refazer um jogo da Copa, põe o vencedor novo na vaga da fase seguinte."""
    games = fixtures(game["edition_id"], database_path)
    nxt = _next_copa_game(game, games)
    if nxt is None or nxt["played_at"]:
        return
    column = "home_user_id" if game["slot"] % 2 == 0 else "away_user_id"
    with connect(database_path) as connection:
        connection.execute(
            f"UPDATE league_fixtures SET {column} = ? WHERE id = ?",
            (game["winner_user_id"], nxt["id"]),
        )


def redraw_copa(
    edition: dict, database_path: str | Path = DATABASE_PATH, *, rng: random.Random | None = None
) -> None:
    """Sorteia a chave da Copa de novo (só antes de a primeira fase ser jogada)."""
    games = fixtures(edition["id"], database_path)
    copa = [g for g in games if g["competition"] == "copa"]
    if not copa:
        raise LeagueError("A Copa IOS só é sorteada depois da final do Pintos.")
    if any(g["played_at"] and g["walkover"] != "folga" for g in copa):
        raise LeagueError("A Copa já começou; não dá para sortear de novo.")
    final = next(g for g in games if g["competition"] == "pintos" and g["stage"] == "final")
    day = min(g["day"] for g in copa)
    with connect(database_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "DELETE FROM league_fixtures WHERE edition_id = ? AND competition = 'copa'",
            (edition["id"],),
        )
        _insert_copa(
            connection,
            edition["id"],
            _copa_seeds(edition["id"], final, database_path),
            day,
            rng or random.SystemRandom(),
        )


def copa_walkover(
    edition: dict, user_id: int | str, database_path: str | Path = DATABASE_PATH
) -> dict:
    """W.O. no próximo jogo da Copa da pessoa: o adversário passa (3 × 0)."""
    user = str(user_id)
    games = [
        g
        for g in fixtures(edition["id"], database_path)
        if g["competition"] == "copa"
        and g["played_at"] is None
        and user in (g["home_user_id"], g["away_user_id"])
    ]
    if not games:
        raise LeagueError("Essa pessoa não tem jogo da Copa marcado.")
    game = games[0]
    home_out = game["home_user_id"] == user
    winner = game["away_user_id"] if home_out else game["home_user_id"]
    events = {"walkover": "mandante" if home_out else "visitante"}
    with connect(database_path) as connection:
        connection.execute(
            """
            UPDATE league_fixtures
            SET home_goals = ?, away_goals = ?, winner_user_id = ?, walkover = ?,
                events_json = ?, played_at = ?
            WHERE id = ? AND played_at IS NULL
            """,
            (
                0 if home_out else WALKOVER_GOALS,
                WALKOVER_GOALS if home_out else 0,
                winner,
                events["walkover"],
                json.dumps(events),
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
                game["id"],
            ),
        )
        row = connection.execute("SELECT * FROM league_fixtures WHERE id = ?", (game["id"],))
        return dict(row.fetchone())
