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
import discord

from src.cogs.league import LeagueCog, LeaguePanel, _score, _title
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

    def test_cup_draw_keeps_champion_and_vice_apart_and_byes_go_to_the_best(self):
        for size in range(4, 17):
            teams = [f"t{n}" for n in range(size)]  # na ordem do Pintos: t0 campeão, t1 vice
            for seed in range(40):
                games = league._copa_draw(teams, random.Random(seed))
                placed = [team for game in games for team in game if team]
                self.assertEqual(sorted(placed), sorted(teams))
                top = {team for game in games[: len(games) // 2] for team in game if team}
                self.assertNotEqual("t0" in top, "t1" in top)
                byes = [home for home, away in games if away is None]
                self.assertEqual(sorted(byes), sorted(teams[: len(byes)]))

    def test_needs_four_complete_teams(self):
        self.teams(3)
        with self.assertRaises(league.LeagueError):
            league.start_edition(555, league.eligible_users(self.db), self.db, now=NOW)
        self.assertEqual(league.current_edition(555, self.db)["status"], "registration")

    def test_only_16_play_and_the_rest_go_first_next_time(self):
        users = self.teams(18)
        edition = league.start_edition(555, users, self.db, now=NOW)
        entries = league.entries(edition["id"], self.db)
        playing = [e["discord_user_id"] for e in entries if not e["waitlisted"]]
        waiting = [e["discord_user_id"] for e in entries if e["waitlisted"]]
        self.assertEqual(playing, users[:16])
        self.assertEqual(waiting, users[16:])
        self.play_edition()
        nxt = league.current_edition(555, self.db)
        self.assertEqual(nxt["number"], 2)
        # 15 rodadas + a final do Pintos (dia 16) + 4 fases da Copa (dias 17 a 20). A próxima
        # não começa no dia da final da Copa, só no seguinte.
        final_day = NOW + timedelta(days=19)
        with self.assertRaises(league.LeagueError):
            league.start_edition(555, users, self.db, now=final_day)
        second = league.start_edition(555, users, self.db, now=final_day + timedelta(days=1))
        order = [e["discord_user_id"] for e in league.entries(second["id"], self.db)]
        self.assertEqual(order[:2], users[16:])

    def test_full_edition_schedule_cup_final_and_prizes(self):
        users = self.teams(6)
        before = {user: get_wallet(int(user), self.db)["memilets"] for user in users}
        league.start_edition(555, users, self.db, now=NOW)
        log = self.play_edition()
        # 6 times: 5 rodadas, a final do Pintos no dia 6 e a Copa (chave de 8) nos dias 7 a 9.
        self.assertEqual(len(log), 9)
        edition = league.history(555, self.db)[0]
        games = league.fixtures(edition["id"], self.db)
        copa = [g for g in games if g["competition"] == "copa"]
        final = next(g for g in games if g["competition"] == "pintos" and g["stage"] == "final")
        self.assertEqual(final["day"], 6)
        self.assertTrue(all(g["day"] > final["day"] for g in copa))  # a Copa vem depois
        # Chave de 8: campeão e vice do Pintos passam direto nas quartas, um em cada lado.
        quarters = sorted((g for g in copa if g["stage"] == "quartas"), key=lambda g: g["slot"])
        self.assertEqual(len(quarters), 4)
        vice = (
            final["away_user_id"]
            if final["winner_user_id"] == final["home_user_id"]
            else final["home_user_id"]
        )
        byes = [g["home_user_id"] for g in quarters if g["walkover"] == "folga"]
        self.assertEqual(set(byes), {final["winner_user_id"], vice})
        top_half = {u for g in quarters[:2] for u in (g["home_user_id"], g["away_user_id"])}
        self.assertNotEqual(final["winner_user_id"] in top_half, vice in top_half)
        self.assertEqual([g["day"] for g in copa if g["stage"] == "final"], [9])
        top = league.standings(edition["id"], self.db)[:2]
        self.assertEqual(
            {final["home_user_id"], final["away_user_id"]}, {t["discord_user_id"] for t in top}
        )
        self.assertEqual(final["home_user_id"], top[0]["discord_user_id"])  # 1º manda a final
        self.assertTrue(final["winner_user_id"])
        self.assertEqual(log[5]["pintos_champion"], final["winner_user_id"])
        self.assertTrue(log[5]["copa_draw"])
        self.assertEqual(log[-1]["copa_champion"], copa[-1]["winner_user_id"])
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
        league.start_edition(555, users, self.db, now=NOW)
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
        edition = league.start_edition(555, users, self.db, now=NOW)
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
            # 4 times: os 2 jogos da rodada 1, narrados (a Copa só vem depois do Pintos).
            titles = [call.kwargs["title"] for call in narrated.await_args_list]
            self.assertEqual(titles, ["Pintos Corridos · Rodada 1"] * 2)
            asyncio.run(cog.run_day(guild, send, manual=True))  # nada mais para hoje
        embeds = [item["embed"] for item in sent if item.get("embed")]
        embeds += [embed for item in sent for embed in item.get("embeds", [])]
        titles = [embed.title for embed in embeds]
        self.assertIn("🏁 Começou a edição 1!", titles)
        self.assertIn("📋 Resultados do dia · Rodada 1", titles)
        self.assertIn("🐣 Pintos Corridos · Tabela", titles)
        # Imagens: início da edição, quadro do dia e tabela.
        names = [item["file"].filename for item in sent if item.get("file")]
        names += [file.filename for item in sent for file in item.get("files", [])]
        self.assertEqual(set(names), {"edicao.png", "dia-pintos.png", "tabela.png"})
        self.assertEqual(sent[-1]["content"], "Não há jogos marcados para hoje.")

    def test_a_whole_edition_through_the_cog_posts_every_image(self):
        self.teams(4)
        cog = LeagueCog(SimpleNamespace(database_path=self.db))
        member = SimpleNamespace(bot=False)
        guild = SimpleNamespace(id=555, get_member=lambda _id: member)
        sent = []

        async def send(**kwargs):
            sent.append(kwargs)
            return AsyncMock()

        module = LeagueCog.run_day.__globals__["league"]
        day = {"value": NOW}
        fakes = {"narrate": AsyncMock(), "score_file": AsyncMock(return_value=None)}
        with (
            patch.dict(LeagueCog.run_day.__globals__, fakes),
            patch.object(module, "today", lambda now=None: day["value"].date()),
        ):
            for _ in range(6):  # 3 rodadas, final do Pintos, semis e final da Copa
                asyncio.run(cog.run_day(guild, send, manual=True))
                day["value"] += timedelta(days=1)
            user = SimpleNamespace(id=1000, display_name="P1000")
            pages = {key: asyncio.run(cog.page(key, 555, user)) for key, *_ in LeaguePanel.PAGES}
        names = [item["file"].filename for item in sent if item.get("file")]
        names += [file.filename for item in sent for file in item.get("files", [])]
        self.assertEqual(names.count("campeao.png"), 2)  # pôster do Pintos e da Copa
        self.assertIn("chave.png", names)  # sorteio e fases da Copa
        self.assertIn("dia-copa.png", names)
        texts = [item.get("content") or "" for item in sent]
        self.assertTrue(any("A edição 2 começa" in text for text in texts))
        self.assertEqual(pages["copa"][1][0].filename, "chave.png")
        self.assertEqual(pages["tabela"][1][0].filename, "tabela.png")
        self.assertIn("Edição 1", pages["campeoes"][0].description)
        view = LeaguePanel(cog, 555, user, "copa")
        styles = {item.label: item.style for item in view.children}
        self.assertEqual(styles["Copa IOS"], discord.ButtonStyle.primary)
        self.assertEqual(styles["Tabela"], discord.ButtonStyle.secondary)


if __name__ == "__main__":
    unittest.main()
