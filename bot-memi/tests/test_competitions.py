import asyncio
import random
import unittest
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from src import league
from src.cogs.league import LeagueCog
from tests.test_admin_tools import LURRAM, NOW, Case


class CompetitionTests(Case):
    def setUp(self):
        super().setUp()
        self.users = [str(1000 + n) for n in range(4)]
        for user in self.users:
            self.add_team(int(user))
        self.edition = league.start_edition(555, self.users, self.db, now=NOW)
        self.rng = random.Random(3)

    def play_day(self, now):
        edition = league.current_edition(555, self.db)
        played = [
            league.play_fixture(f, self.rng, self.db)
            for f in league.due_fixtures(edition, now, self.db)
        ]
        return played, league.advance(edition, self.db, now=now)

    def play_pintos(self):
        """3 rodadas e a final (4 times); devolve o dia seguinte, quando a Copa começa."""
        day = NOW
        for _ in range(4):
            self.play_day(day)
            day += timedelta(days=1)
        return day

    def cup(self, stage=None):
        return [
            g
            for g in league.fixtures(self.edition["id"], self.db)
            if g["competition"] == "copa" and (stage is None or g["stage"] == stage)
        ]

    def test_advance_per_competition_refuses_the_other_one(self):
        with self.assertRaises(league.LeagueError) as error:
            league.pull_forward(self.edition, self.db, now=NOW, competition="copa")
        self.assertIn("depois da final", str(error.exception))
        self.play_day(NOW)
        moved = league.pull_forward(self.edition, self.db, now=NOW, competition="pintos")
        stages = {g["stage"] for g in league.due_fixtures(moved, NOW, self.db)}
        self.assertEqual(stages, {"rodada 2"})

    def test_end_pintos_drops_the_rest_and_schedules_the_final(self):
        with self.assertRaises(league.LeagueError):
            league.end_pintos(self.edition, self.db, now=NOW)  # nenhuma rodada jogada
        self.play_day(NOW)
        top = [t["discord_user_id"] for t in league.standings(self.edition["id"], self.db)][:2]
        final = league.end_pintos(self.edition, self.db, now=NOW)
        self.assertEqual([final["home_user_id"], final["away_user_id"]], top)
        self.assertEqual(final["day"], 2)  # o próximo dia de jogos
        pintos = [
            g for g in league.fixtures(self.edition["id"], self.db) if g["competition"] == "pintos"
        ]
        self.assertTrue(all(g["played_at"] or g["stage"] == "final" for g in pintos))
        with self.assertRaises(league.LeagueError):
            league.end_pintos(self.edition, self.db, now=NOW)
        self.assertEqual(league.current_round(self.edition["id"], self.db), 1)
        self.assertEqual(league.round_games(self.edition["id"], 4, self.db), [])  # 3 + final

    def test_replay_only_today_and_never_a_final(self):
        self.play_day(NOW)
        game = league.replayable(self.edition, self.users[0], self.db, now=NOW)
        self.assertIn(self.users[0], (game["home_user_id"], game["away_user_id"]))
        fresh = league.reset_fixture(game, self.db)
        self.assertIsNone(fresh["played_at"])
        again = league.play_fixture(fresh, random.Random(9), self.db)
        self.assertIsNotNone(again["played_at"])
        table = league.standings(self.edition["id"], self.db)
        self.assertEqual(sum(t["played"] for t in table), 4)  # não conta duas vezes
        with self.assertRaises(league.LeagueError):
            league.replayable(self.edition, self.users[0], self.db, now=NOW + timedelta(days=1))
        day = NOW + timedelta(days=1)
        for _ in range(3):
            self.play_day(day)
            day += timedelta(days=1)
        final = next(
            g
            for g in league.fixtures(self.edition["id"], self.db)
            if g["competition"] == "pintos" and g["stage"] == "final"
        )
        with self.assertRaises(league.LeagueError) as error:
            league.replayable(
                self.edition, final["home_user_id"], self.db, now=day - timedelta(days=1)
            )
        self.assertIn("Final", str(error.exception))

    def test_redraw_before_the_cup_starts_and_walkover(self):
        cup_day = self.play_pintos()
        league.redraw_copa(self.edition, self.db, rng=random.Random(1))
        after = self.cup()
        self.assertEqual(len(after), 2)  # semis de novo, ainda sem jogar
        self.assertFalse(any(g["played_at"] for g in after))
        loser = after[0]["home_user_id"]
        game = league.copa_walkover(self.edition, loser, self.db)
        self.assertEqual(game["winner_user_id"], after[0]["away_user_id"])
        self.assertEqual(game["walkover"], "mandante")
        with self.assertRaises(league.LeagueError):
            league.redraw_copa(self.edition, self.db)  # já começou
        with self.assertRaises(league.LeagueError):
            league.copa_walkover(self.edition, loser, self.db)  # eliminado, sem jogo marcado
        self.play_day(cup_day)
        final = self.cup("final")
        self.assertEqual(len(final), 1)
        self.assertIn(game["winner_user_id"], (final[0]["home_user_id"], final[0]["away_user_id"]))

    def test_replayed_cup_game_updates_the_next_stage(self):
        cup_day = self.play_pintos()
        played, _ = self.play_day(cup_day)
        semi = played[0]
        column = "home_user_id" if semi["slot"] % 2 == 0 else "away_user_id"
        self.assertEqual(self.cup("final")[0][column], semi["winner_user_id"])
        game = league.replayable(self.edition, semi["home_user_id"], self.db, now=cup_day)
        fresh = league.reset_fixture(game, self.db)
        replayed = league.play_fixture(fresh, random.Random(7), self.db)
        league.sync_copa_winner(replayed, self.db)
        self.assertEqual(self.cup("final")[0][column], replayed["winner_user_id"])


class CompetitionCommandTests(Case):
    def test_round_command_end_confirmation_and_cup_before_time(self):
        users = [str(1000 + n) for n in range(4)]
        for user in users:
            self.add_team(int(user))
        league.start_edition(555, users, self.db, now=NOW)
        cog = LeagueCog(SimpleNamespace(database_path=self.db))
        context = SimpleNamespace(
            author=SimpleNamespace(
                id=LURRAM, guild_permissions=SimpleNamespace(manage_guild=False)
            ),
            guild=SimpleNamespace(id=555),
            reply=AsyncMock(),
            send=AsyncMock(),
        )
        module = LeagueCog.pintos_round.callback.__globals__["league"]
        with patch.object(module, "today", lambda now=None: NOW.date()):
            asyncio.run(LeagueCog.pintos_round.callback(cog, context, 1))
            kwargs = context.reply.await_args.kwargs
            self.assertEqual(kwargs["file"].filename, "rodada.png")
            self.assertIn("ainda não jogada", kwargs["embed"].title)
            asyncio.run(LeagueCog.pintos_round.callback(cog, context, 9))
            self.assertIn("3 rodadas", context.reply.await_args.args[0])
            asyncio.run(LeagueCog.pintos_end.callback(cog, context, None))
            self.assertIn("confirmar", context.reply.await_args.args[0])
            asyncio.run(LeagueCog.copa_advance.callback(cog, context))
            self.assertIn("depois da final", context.reply.await_args.args[0])


if __name__ == "__main__":
    unittest.main()
