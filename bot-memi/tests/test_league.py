import asyncio
import random
import tempfile
import unittest
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from src import league
from src.cogs.league import LeagueCog, _score, _title
from src.db import connect, initialize_database
from src.game import claim_player, get_wallet, register_user
from src.team_stats import compute_stats, match_records
from src.teams import FORMATIONS, ensure_team, scale_player

NOW = datetime(2026, 11, 2, 22, tzinfo=timezone.utc)  # 19h em Brasília


class LeagueTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db = Path(self.tempdir.name) / "league.sqlite"
        initialize_database(self.db)

    def tearDown(self):
        self.tempdir.cleanup()

    def add_team(self, user_id, rating=75):
        register_user(user_id, f"P{user_id}", self.db)
        ensure_team(user_id, f"Time {user_id}", self.db)
        for index, position in enumerate(FORMATIONS["4-3-3"]):
            with connect(self.db) as connection:
                player = connection.execute(
                    """
                    INSERT INTO players (name, display_name, game_position, overall,
                                         activity_status, phase2_candidate)
                    VALUES (?, ?, ?, ?, 'active', 0)
                    """,
                    (f"J{user_id}-{index}", f"J{index}", position, rating),
                ).lastrowid
            claim_player(player, user_id, self.db)
            scale_player(user_id, player, self.db)

    def teams(self, count):
        users = [str(1000 + n) for n in range(count)]
        for n, user in enumerate(users):
            self.add_team(int(user), 70 + n)
        return users

    def play_edition(self, now=NOW, rng=None):
        """Joga a edição atual dia a dia até o fim; devolve (dias, novidades de cada dia)."""
        generator = rng or random.Random(5)
        log = []
        while True:
            edition = league.current_edition(555, self.db)
            if edition["status"] != "playing":
                return log
            for fixture in league.due_fixtures(edition, now, self.db):
                league.play_fixture(fixture, generator, self.db)
            log.append(league.advance(edition, self.db, now=now))
            now += timedelta(days=1)
            self.assertLess(len(log), 40)

    def test_round_robin_pairs_everyone_once_and_one_game_per_day(self):
        for size in (4, 5, 16):
            teams = [str(n) for n in range(size)]
            rounds = league._round_robin(teams)
            self.assertEqual(len(rounds), size if size % 2 else size - 1)
            pairs = Counter(frozenset(game) for games in rounds for game in games)
            self.assertEqual(len(pairs), size * (size - 1) // 2)
            self.assertEqual(set(pairs.values()), {1})
            for games in rounds:
                playing = [team for game in games for team in game]
                self.assertEqual(len(playing), len(set(playing)))
            homes = Counter(home for games in rounds for home, _ in games)
            self.assertLessEqual(max(homes.values()) - min(homes.values()), 2)

    def test_needs_four_complete_teams(self):
        self.teams(3)
        with self.assertRaises(league.LeagueError):
            league.start_edition(555, league.eligible_users(self.db), self.db, now=NOW)
        self.assertEqual(league.current_edition(555, self.db)["status"], "registration")

    def test_only_16_play_and_the_rest_go_first_next_time(self):
        users = self.teams(18)
        edition = league.start_edition(555, users, self.db, now=NOW, rng=random.Random(1))
        entries = league.entries(edition["id"], self.db)
        playing = [e["discord_user_id"] for e in entries if not e["waitlisted"]]
        waiting = [e["discord_user_id"] for e in entries if e["waitlisted"]]
        self.assertEqual(playing, users[:16])
        self.assertEqual(waiting, users[16:])
        self.play_edition()
        nxt = league.current_edition(555, self.db)
        self.assertEqual(nxt["number"], 2)
        # A próxima não começa no dia da final, só no seguinte.
        final_day = NOW + timedelta(days=15)
        with self.assertRaises(league.LeagueError):
            league.start_edition(555, users, self.db, now=final_day)
        second = league.start_edition(555, users, self.db, now=final_day + timedelta(days=1))
        order = [e["discord_user_id"] for e in league.entries(second["id"], self.db)]
        self.assertEqual(order[:2], users[16:])

    def test_full_edition_schedule_cup_final_and_prizes(self):
        users = self.teams(6)
        before = {user: get_wallet(int(user), self.db)["memilets"] for user in users}
        league.start_edition(555, users, self.db, now=NOW, rng=random.Random(2))
        log = self.play_edition()
        # 6 times: 5 rodadas + a final no dia 6.
        self.assertEqual(len(log), 6)
        edition = league.history(555, self.db)[0]
        games = league.fixtures(edition["id"], self.db)
        copa = [g for g in games if g["competition"] == "copa"]
        # Chave de 8: 2 folgas nas quartas, depois semi e final.
        self.assertEqual([g["stage"] for g in copa].count("quartas"), 4)
        self.assertEqual(sum(g["walkover"] == "folga" for g in copa), 2)
        self.assertEqual([g["day"] for g in copa if g["stage"] == "final"], [3])
        final = next(g for g in games if g["competition"] == "pintos" and g["stage"] == "final")
        top = league.standings(edition["id"], self.db)[:2]
        self.assertEqual(
            {final["home_user_id"], final["away_user_id"]}, {t["discord_user_id"] for t in top}
        )
        self.assertEqual(final["home_user_id"], top[0]["discord_user_id"])  # 1º manda a final
        self.assertTrue(final["winner_user_id"])
        self.assertEqual(log[2]["copa_champion"], copa[-1]["winner_user_id"])
        self.assertEqual(log[-1]["pintos_champion"], final["winner_user_id"])
        with connect(self.db) as connection:
            prizes = {
                (row["competition"], row["discord_user_id"]): row["memilets"]
                for row in connection.execute("SELECT * FROM league_prizes")
            }
            items = connection.execute(
                "SELECT item, SUM(amount) FROM user_inventory WHERE discord_user_id = ? "
                "GROUP BY item",
                (final["winner_user_id"],),
            ).fetchall()
        self.assertEqual(sum(1 for key in prizes if key[0] == "pintos"), 6)  # todos ganham algo
        champion = final["winner_user_id"]
        wins = next(
            t["wins"]
            for t in league.standings(edition["id"], self.db)
            if t["discord_user_id"] == champion
        )
        self.assertEqual(prizes[("pintos", champion)], 15_000 + 150 * wins)
        self.assertIn("pacote_epico", {row[0] for row in items})
        for user in users:
            paid = sum(value for (_, who), value in prizes.items() if who == user)
            self.assertEqual(get_wallet(int(user), self.db)["memilets"], before[user] + paid)
        # Pagar de novo não paga duas vezes.
        news = league.advance(league.history(555, self.db)[0], self.db)
        self.assertEqual(news["prizes"], {})
        self.assertEqual(league.current_edition(555, self.db)["status"], "registration")

    def test_stats_count_league_games_and_titles(self):
        users = self.teams(4)
        league.start_edition(555, users, self.db, now=NOW, rng=random.Random(3))
        self.play_edition()
        edition = league.history(555, self.db)[0]
        champion = edition_champion = next(
            g["winner_user_id"]
            for g in league.fixtures(edition["id"], self.db)
            if g["competition"] == "pintos" and g["stage"] == "final"
        )
        stats = compute_stats(match_records(champion, self.db))
        self.assertGreaterEqual(stats.titles, 1)
        self.assertGreaterEqual(stats.matches, 4)  # 3 rodadas + a final, fora a Copa
        name = league.team_info(self.db)[edition_champion]["team_name"]
        self.assertEqual(edition["pintos_campeao"], name)

    def test_team_without_lineup_loses_by_walkover(self):
        users = self.teams(4)
        edition = league.start_edition(555, users, self.db, now=NOW, rng=random.Random(4))
        with connect(self.db) as connection:
            connection.execute("DELETE FROM team_lineups WHERE discord_user_id = ?", (users[0],))
        for fixture in league.due_fixtures(edition, NOW, self.db):
            league.play_fixture(fixture, random.Random(4), self.db)
        games = [
            g
            for g in league.fixtures(edition["id"], self.db)
            if users[0] in (g["home_user_id"], g["away_user_id"]) and g["played_at"]
        ]
        self.assertTrue(games)
        for game in games:
            self.assertIn(game["walkover"], {"mandante", "visitante"})
            self.assertNotEqual(game["winner_user_id"], users[0])
            self.assertEqual(sorted((game["home_goals"], game["away_goals"])), [0, 3])
        table = {t["discord_user_id"]: t for t in league.standings(edition["id"], self.db)}
        self.assertEqual(table[users[0]]["points"], 0)

    def test_titles_and_scores_for_the_channel(self):
        game = {
            "competition": "copa",
            "stage": "quartas",
            "home_user_id": "1",
            "away_user_id": "2",
            "home_goals": 1,
            "away_goals": 1,
            "home_penalties": 4,
            "away_penalties": 3,
            "walkover": None,
            "played_at": "x",
        }
        names = {"1": "Ana FC", "2": "Beto FC"}
        self.assertEqual(_title(game), "Copa IOS · Quartas de final")
        self.assertEqual(_title({**game, "stage": "final"}), "FINAL da Copa IOS")
        self.assertEqual(
            _title({**game, "competition": "pintos", "stage": "rodada 3"}),
            "Pintos Corridos · Rodada 3",
        )
        self.assertEqual(_score(game, names), "Ana FC **1 × 1** Beto FC (pên. 4 × 3)")
        self.assertEqual(_score({**game, "played_at": None}, names), "Ana FC × Beto FC")

    def test_the_seven_pm_run_starts_plays_narrates_and_summarizes(self):
        self.teams(4)
        cog = LeagueCog(SimpleNamespace(database_path=self.db))
        member = SimpleNamespace(bot=False)
        guild = SimpleNamespace(id=555, get_member=lambda _id: member)
        sent = []

        async def send(**kwargs):
            sent.append(kwargs)
            return AsyncMock()

        narrated = AsyncMock()
        # Direto no módulo do cog: outro teste pode ter recarregado `src.cogs.league`.
        fakes = {"narrate": narrated, "score_file": AsyncMock(return_value=None)}
        with patch.dict(LeagueCog.run_day.__globals__, fakes):
            asyncio.run(cog.run_day(guild, send, manual=True))
            # 4 times: 2 semis da Copa + 2 jogos da rodada 1, todos narrados.
            self.assertEqual(narrated.await_count, 4)
            titles = [call.kwargs["title"] for call in narrated.await_args_list]
            self.assertEqual(titles[:2], ["Copa IOS · Semifinal"] * 2)
            self.assertEqual(titles[2:], ["Pintos Corridos · Rodada 1"] * 2)
            asyncio.run(cog.run_day(guild, send, manual=True))  # nada mais para hoje
        embeds = [item["embed"].title for item in sent if item.get("embed")]
        self.assertIn("🏁 Começou a edição 1!", embeds)
        self.assertIn("📋 Resultados do dia", embeds)
        self.assertTrue(any(item.get("file") for item in sent))  # a tabela
        self.assertEqual(sent[-1]["content"], "Não há jogos marcados para hoje.")


if __name__ == "__main__":
    unittest.main()
