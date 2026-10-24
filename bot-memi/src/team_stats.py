"""Estatísticas dos times (`mm!stats`), juntando campeonato e amistosos.

O campeonato já guarda cada jogo em `championship_fixtures`. Os amistosos passam a ser
guardados em `friendly_matches` (`record_friendly`) a partir desta versão; os jogados
antes não têm registro. Os filtros são por tipo (campeonato/amistoso), período e
adversário (confronto direto).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from src.db import DATABASE_PATH, connect

KINDS = {"all": "Tudo", "championship": "Campeonato", "friendly": "Amistosos"}
PERIODS = {"all": "Sempre", "month": "Este mês", "week": "Últimos 7 dias"}


@dataclass(frozen=True)
class MatchRecord:
    kind: str
    played_at: datetime
    opponent_id: str
    goals_for: int
    goals_against: int
    outcome: str  # "V", "E" ou "D" (pênaltis decidem no amistoso e na final)
    penalties: tuple[int, int] | None = None
    final: bool = False
    scorers: tuple[str, ...] = ()
    highlights: tuple[str, ...] = ()


@dataclass
class TeamStats:
    matches: int = 0
    wins: int = 0
    draws: int = 0
    losses: int = 0
    goals_for: int = 0
    goals_against: int = 0
    clean_sheets: int = 0
    titles: int = 0
    hat_tricks: int = 0
    shootouts: tuple[int, int] = (0, 0)  # disputas de pênaltis vencidas e perdidas
    biggest_win: MatchRecord | None = None
    streak: tuple[str, int] = ("", 0)
    form: list[str] = field(default_factory=list)
    top_scorers: list[tuple[str, int]] = field(default_factory=list)
    opponents: dict[str, int] = field(default_factory=dict)

    @property
    def win_rate(self) -> float:
        return self.wins / self.matches if self.matches else 0.0


def record_friendly(
    home_user_id: int | str,
    away_user_id: int | str,
    result: Any,
    penalties: tuple[int, int] | None = None,
    database_path: str | Path = DATABASE_PATH,
    *,
    now: datetime | None = None,
) -> int:
    """Guarda o amistoso com placar, pênaltis, gols e destaques (para o `mm!stats`)."""
    data = result.to_dict()
    details = {
        "goals": data["goals"],
        "highlights": (data.get("stats") or {}).get("highlights", []),
    }
    moment = (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")
    with connect(database_path) as connection:
        return int(
            connection.execute(
                """
                INSERT INTO friendly_matches (
                    played_at, home_user_id, away_user_id, home_goals, away_goals,
                    home_penalties, away_penalties, details_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    moment,
                    str(home_user_id),
                    str(away_user_id),
                    result.home_goals,
                    result.away_goals,
                    penalties[0] if penalties else None,
                    penalties[1] if penalties else None,
                    json.dumps(details, ensure_ascii=False),
                ),
            ).lastrowid
        )


def _moment(value: str | None) -> datetime:
    if not value:
        return datetime(2000, 1, 1, tzinfo=timezone.utc)
    moment = datetime.fromisoformat(value.replace(" ", "T"))
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _side_details(goals, highlights, side: str, team_name: str | None):
    scorers = tuple(
        str(goal.get("scorer_name") or "—")
        for goal in goals
        if goal.get("side") == side and goal.get("play") != "contra"
    )
    own = tuple(h for h in highlights if team_name and h.endswith(f"({team_name})"))
    return scorers, own


def match_records(
    discord_user_id: int | str, database_path: str | Path = DATABASE_PATH
) -> list[MatchRecord]:
    """Todos os jogos do time (campeonato e amistosos), do mais recente ao mais antigo."""
    user_id = str(discord_user_id)
    records: list[MatchRecord] = []
    with connect(database_path) as connection:
        name_row = connection.execute(
            "SELECT team_name FROM game_teams WHERE discord_user_id = ?", (user_id,)
        ).fetchone()
        team_name = name_row["team_name"] if name_row else None
        fixtures = connection.execute(
            """
            SELECT * FROM championship_fixtures
            WHERE (home_user_id = ? OR away_user_id = ?) AND home_goals IS NOT NULL
            """,
            (user_id, user_id),
        ).fetchall()
        friendlies = connection.execute(
            "SELECT * FROM friendly_matches WHERE home_user_id = ? OR away_user_id = ?",
            (user_id, user_id),
        ).fetchall()
    for row in fixtures:
        home = row["home_user_id"] == user_id
        own, other = (row["home_goals"], row["away_goals"])[:: 1 if home else -1]
        events = json.loads(row["events_json"] or "{}")
        penalties = events.get("penalties")
        if row["is_final"] and row["winner_user_id"]:
            outcome = "V" if row["winner_user_id"] == user_id else "D"
        else:
            outcome = "V" if own > other else "D" if own < other else "E"
        scorers, highlights = _side_details(
            events.get("goals", []),
            (events.get("stats") or {}).get("highlights", []),
            "home" if home else "away",
            team_name,
        )
        records.append(
            MatchRecord(
                "championship",
                _moment(row["played_at"]),
                row["away_user_id"] if home else row["home_user_id"],
                int(own),
                int(other),
                outcome,
                tuple(penalties[:: 1 if home else -1]) if penalties else None,
                bool(row["is_final"]),
                scorers,
                highlights,
            )
        )
    for row in friendlies:
        home = row["home_user_id"] == user_id
        own, other = (row["home_goals"], row["away_goals"])[:: 1 if home else -1]
        penalties = None
        if row["home_penalties"] is not None:
            penalties = (row["home_penalties"], row["away_penalties"])[:: 1 if home else -1]
        if own != other:
            outcome = "V" if own > other else "D"
        elif penalties:
            outcome = "V" if penalties[0] > penalties[1] else "D"
        else:
            outcome = "E"
        details = json.loads(row["details_json"] or "{}")
        scorers, highlights = _side_details(
            details.get("goals", []),
            details.get("highlights", []),
            "home" if home else "away",
            team_name,
        )
        records.append(
            MatchRecord(
                "friendly",
                _moment(row["played_at"]),
                row["away_user_id"] if home else row["home_user_id"],
                int(own),
                int(other),
                outcome,
                tuple(penalties) if penalties else None,
                False,
                scorers,
                highlights,
            )
        )
    records.sort(key=lambda record: record.played_at, reverse=True)
    return records


def filter_records(
    records: list[MatchRecord],
    *,
    kind: str = "all",
    period: str = "all",
    opponent: str | None = None,
    now: datetime | None = None,
) -> list[MatchRecord]:
    current = now or datetime.now(timezone.utc)
    start = None
    if period == "week":
        start = current - timedelta(days=7)
    elif period == "month":
        start = current.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return [
        record
        for record in records
        if (kind == "all" or record.kind == kind)
        and (start is None or record.played_at >= start)
        and (opponent is None or record.opponent_id == opponent)
    ]


def compute_stats(records: list[MatchRecord]) -> TeamStats:
    """Números do recorte (os registros vêm do mais recente ao mais antigo)."""
    stats = TeamStats()
    scorers: dict[str, int] = {}
    won, lost = 0, 0
    for record in records:
        stats.matches += 1
        stats.goals_for += record.goals_for
        stats.goals_against += record.goals_against
        if record.goals_against == 0:
            stats.clean_sheets += 1
        if record.outcome == "V":
            stats.wins += 1
            margin = record.goals_for - record.goals_against
            best = stats.biggest_win
            if margin > 0 and (
                best is None
                or (margin, record.goals_for)
                > (best.goals_for - best.goals_against, best.goals_for)
            ):
                stats.biggest_win = record
            if record.final:
                stats.titles += 1
        elif record.outcome == "D":
            stats.losses += 1
        else:
            stats.draws += 1
        if record.penalties:
            won += record.outcome == "V"
            lost += record.outcome == "D"
        for name in record.scorers:
            scorers[name] = scorers.get(name, 0) + 1
        stats.hat_tricks += sum(1 for h in record.highlights if h.startswith(("🎩", "🔥", "🖐️")))
        stats.opponents[record.opponent_id] = stats.opponents.get(record.opponent_id, 0) + 1
    stats.shootouts = (won, lost)
    stats.form = [record.outcome for record in records[:5]]
    if records:
        first = records[0].outcome
        length = next(
            (index for index, record in enumerate(records) if record.outcome != first),
            len(records),
        )
        stats.streak = (first, length)
    stats.top_scorers = sorted(scorers.items(), key=lambda item: (-item[1], item[0]))[:5]
    return stats


@dataclass(frozen=True)
class Rivalry:
    """Confronto direto entre dois times (campeonato e amistosos), do mais antigo ao mais
    recente. `outcomes` é do ponto de vista do primeiro time: "V", "E" ou "D"."""

    outcomes: tuple[str, ...] = ()

    @property
    def games(self) -> int:
        return len(self.outcomes)

    @property
    def wins(self) -> int:
        return self.outcomes.count("V")

    @property
    def draws(self) -> int:
        return self.outcomes.count("E")

    @property
    def losses(self) -> int:
        return self.outcomes.count("D")

    def flipped(self) -> "Rivalry":
        """O mesmo confronto do ponto de vista do outro time."""
        swap = {"V": "D", "D": "V", "E": "E"}
        return Rivalry(tuple(swap[outcome] for outcome in self.outcomes))

    def run(self, outcome: str = "V", skip_last: bool = False) -> int:
        """Quantos jogos seguidos, contando do fim, deram `outcome`."""
        outcomes = self.outcomes[:-1] if skip_last else self.outcomes
        count = 0
        for item in reversed(outcomes):
            if item != outcome:
                break
            count += 1
        return count


def rivalry(
    first_user_id: int | str, second_user_id: int | str, database_path: str | Path = DATABASE_PATH
) -> Rivalry:
    """O confronto direto do primeiro time contra o segundo."""
    other = str(second_user_id)
    records = [r for r in match_records(first_user_id, database_path) if r.opponent_id == other]
    records.sort(key=lambda record: record.played_at)
    return Rivalry(tuple(record.outcome for record in records))
