import json
import random
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.db import connect, initialize_database
from src.game import register_user
from src.simulation import simulate_match
from src.team_stats import compute_stats, filter_records, match_records, record_friendly
from src.teams import FORMATIONS, ensure_team

NOW = datetime(2026, 10, 23, 18, tzinfo=timezone.utc)


def lineup(prefix, overall, first_id=1):
    return [
        {"id": first_id + i, "name": f"{prefix}{i}", "position": p, "effective_overall": overall}
        for i, p in enumerate(FORMATIONS["4-3-3"])
    ]


class TeamStatsTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.tempdir.name) / "stats.sqlite"
        initialize_database(self.database_path)
        for user in (100, 200, 300):
            register_user(user, f"P{user}", self.database_path)
            ensure_team(user, f"Time {user}", self.database_path)

    def tearDown(self):
        self.tempdir.cleanup()

    def championship(
        self,
        home,
        away,
        home_goals,
        away_goals,
        *,
        final=False,
        winner=None,
        penalties=None,
        played_at=NOW,
        goals=(),
    ):
        with connect(self.database_path) as connection:
            champ = connection.execute(
                "INSERT INTO championships (guild_id, competition_date, timezone, status) "
                "VALUES ('g', ?, 'UTC', 'completed')",
                (played_at.isoformat() + str(random.random()),),
            ).lastrowid
            events = {"goals": list(goals)}
            if penalties:
                events["penalties"] = list(penalties)
            connection.execute(
                """
                INSERT INTO championship_fixtures (
                    championship_id, round_no, match_order, home_user_id, away_user_id,
                    is_final, home_goals, away_goals, events_json, winner_user_id, played_at
                ) VALUES (?, 1, 1, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    champ,
                    str(home),
                    str(away),
                    int(final),
                    home_goals,
                    away_goals,
                    json.dumps(events),
                    winner,
                    played_at.isoformat(),
                ),
            )

    def friendly(self, seed, now):
        result = simulate_match(
            "A", lineup("a", 85), "B", lineup("b", 70, 100), rng=random.Random(seed)
        )
        penalties = (5, 4) if result.home_goals == result.away_goals else None
        record_friendly(100, 200, result, penalties, self.database_path, now=now)
        return result, penalties

    def test_championship_and_friendlies_are_combined_and_filtered(self):
        self.championship(
            100,
            300,
            3,
            0,
            played_at=NOW - timedelta(days=20),
            goals=[{"side": "home", "scorer_name": "Eusébio"}] * 3,
        )
        self.championship(
            300,
            100,
            1,
            1,
            final=True,
            winner="100",
            penalties=(3, 4),
            played_at=NOW - timedelta(days=2),
        )
        result, penalties = self.friendly(1, NOW - timedelta(days=1))
        records = match_records(100, self.database_path)
        self.assertEqual([r.kind for r in records], ["friendly", "championship", "championship"])
        stats = compute_stats(records)
        self.assertEqual(stats.matches, 3)
        self.assertEqual(stats.titles, 1)  # a final vencida nos pênaltis conta
        self.assertEqual(stats.shootouts[0], 1 + (1 if penalties else 0))
        self.assertIn(("Eusébio", 3), stats.top_scorers)
        championship = filter_records(records, kind="championship", now=NOW)
        self.assertEqual(compute_stats(championship).goals_for, 4)
        week = filter_records(records, period="week", now=NOW)
        self.assertEqual(len(week), 2)
        rival = filter_records(records, opponent="300", now=NOW)
        self.assertEqual(len(rival), 2)

    def test_numbers_add_up(self):
        for seed in range(12):
            self.friendly(seed, NOW - timedelta(hours=seed))
        stats = compute_stats(match_records(100, self.database_path))
        self.assertEqual(stats.matches, 12)
        self.assertEqual(stats.wins + stats.draws + stats.losses, 12)
        self.assertEqual(stats.draws, 0)  # amistoso empatado vai para os pênaltis
        self.assertLessEqual(len(stats.form), 5)
        self.assertGreaterEqual(stats.streak[1], 1)
        if stats.biggest_win:
            self.assertGreater(stats.biggest_win.goals_for, stats.biggest_win.goals_against)
        away = compute_stats(match_records(200, self.database_path))
        self.assertEqual((away.wins, away.goals_for), (stats.losses, stats.goals_against))


class StatsViewTests(unittest.IsolatedAsyncioTestCase):
    async def test_panel_shows_numbers_and_filters(self):
        tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(tempdir.cleanup)
        database_path = Path(tempdir.name) / "view.sqlite"
        initialize_database(database_path)
        for user in (100, 200):
            register_user(user, f"P{user}", database_path)
            ensure_team(user, f"Time {user}", database_path)
        result = simulate_match(
            "A", lineup("a", 90), "B", lineup("b", 60, 100), rng=random.Random(3)
        )
        record_friendly(100, 200, result, None, database_path)
        from src.cogs.matches import StatsView

        view = StatsView(100, 100, database_path)
        embed = view.embed()
        fields = {field.name: field.value for field in embed.fields}
        self.assertIn("1** jogos", fields["Campanha"])
        view.kind = "championship"
        self.assertIn("Nenhum jogo", view.embed().description)
        opponents = view.children[2]
        self.assertEqual([o.value for o in opponents.options], ["all", "200"])


if __name__ == "__main__":
    unittest.main()
