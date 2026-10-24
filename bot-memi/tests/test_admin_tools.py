import asyncio
from contextlib import closing
import gzip
import random
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from src import admin_tools, league
from src.admin_tools import AdminError
from src.cogs.league import LeagueCog, hour_label, parse_time
from src.db import connect, initialize_database
from src.game import claim_player, get_wallet, register_user
from src.teams import FORMATIONS, ensure_team, get_valid_lineup, scale_player

NOW = datetime(2026, 11, 2, 22, tzinfo=timezone.utc)
LURRAM = 302873192706605057


class Case(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db = Path(self.tempdir.name) / "jogo.sqlite"
        initialize_database(self.db)

    def tearDown(self):
        self.tempdir.cleanup()

    def add_player(self, name, position="CA", overall=80):
        with connect(self.db) as connection:
            return connection.execute(
                """
                INSERT INTO players (name, display_name, game_position, overall,
                                     activity_status, phase2_candidate)
                VALUES (?, ?, ?, ?, 'active', 0)
                """,
                (name, name, position, overall),
            ).lastrowid

    def add_team(self, user_id, rating=75):
        register_user(user_id, f"P{user_id}", self.db)
        ensure_team(user_id, f"Time {user_id}", self.db)
        for index, position in enumerate(FORMATIONS["4-3-3"]):
            player = self.add_player(f"J{user_id}-{index}", position, rating)
            claim_player(player, user_id, self.db)
            scale_player(user_id, player, self.db)


class EconomyToolTests(Case):
    def test_memilets_go_up_and_never_below_zero(self):
        change, balance = admin_tools.adjust_memilets(1, "Ana", 5_000, self.db)
        self.assertEqual(change, 5_000)
        self.assertEqual(balance, get_wallet(1, self.db)["memilets"])
        change, after = admin_tools.adjust_memilets(1, "Ana", -999_999, self.db)
        self.assertEqual((change, after), (-balance, 0))  # tira só o que tinha

    def test_items_by_name_and_removal(self):
        self.assertEqual(admin_tools.item_key("caixa épica"), "caixa_epica")
        self.assertEqual(admin_tools.item_key("Pacote_Epico"), "pacote_epico")
        with self.assertRaises(AdminError):
            admin_tools.item_key("espada")
        self.assertEqual(admin_tools.adjust_items(1, "Ana", "caixa_epica", 3, self.db), 3)
        self.assertEqual(admin_tools.adjust_items(1, "Ana", "caixa_epica", -5, self.db), 0)

    def test_give_free_player_and_force_from_another_owner(self):
        free = self.add_player("Zé Livre")
        given = admin_tools.give_player("Zé Livre", 1, "Ana", self.db)
        self.assertEqual(int(given.player["id"]), free)
        self.assertIsNone(given.previous_owner)
        with self.assertRaises(AdminError):
            admin_tools.give_player(str(free), 1, "Ana", self.db)  # já é dela
        # Titular do Beto: sem `force` não sai; com `force`, sai do time dele.
        self.add_team(2)
        starter = int(get_valid_lineup(2, self.db)[1][0]["id"])
        with self.assertRaises(AdminError):
            admin_tools.give_player(str(starter), 1, "Ana", self.db)
        given = admin_tools.give_player(str(starter), 1, "Ana", self.db, force=True)
        self.assertEqual(given.previous_owner, "2")
        with connect(self.db) as connection:
            lineup = [
                r[0]
                for r in connection.execute(
                    "SELECT player_id FROM team_lineups WHERE discord_user_id = '2'"
                )
            ]
        self.assertNotIn(starter, lineup)
        with connect(self.db) as connection:
            owner = connection.execute(
                "SELECT discord_user_id FROM player_ownership WHERE player_id = ?", (starter,)
            ).fetchone()[0]
        self.assertEqual(owner, "1")

    def test_ambiguous_or_missing_player(self):
        self.add_player("Ronaldo Silva")
        self.add_player("Ronaldo Souza")
        with self.assertRaises(AdminError) as error:
            admin_tools.give_player("Ronaldo", 1, "Ana", self.db)
        self.assertIn("Use o ID", str(error.exception))
        with self.assertRaises(AdminError):
            admin_tools.give_player("Ninguém Mesmo", 1, "Ana", self.db)

    def test_reset_rolls_and_claim(self):
        register_user(1, "Ana", self.db)
        cycle = admin_tools.cycle_start_for(NOW).isoformat(timespec="seconds")
        with connect(self.db) as connection:
            connection.execute(
                "INSERT INTO roll_cycles (discord_user_id, cycle_started_at, rolls_used) "
                "VALUES ('1', ?, 8)",
                (cycle,),
            )
            connection.execute(
                "UPDATE game_users SET last_claim_at = ? WHERE discord_user_id = '1'",
                (NOW.isoformat(),),
            )
        admin_tools.reset_rolls(1, self.db, now=NOW)
        admin_tools.reset_claim(1, self.db)
        with connect(self.db) as connection:
            self.assertIsNone(connection.execute("SELECT 1 FROM roll_cycles").fetchone())
            self.assertIsNone(
                connection.execute("SELECT last_claim_at FROM game_users").fetchone()[0]
            )

    def test_backup_is_a_readable_copy_and_keeps_the_last_ten(self):
        register_user(1, "Ana", self.db)
        for minute in range(12):
            path = admin_tools.backup_database(self.db, now=NOW + timedelta(minutes=minute))
        self.assertEqual(len(list(path.parent.glob("jogo-*.sqlite.gz"))), admin_tools.BACKUP_KEEP)
        self.assertEqual(admin_tools.last_backup(self.db), path)
        restored = Path(self.tempdir.name) / "restaurado.sqlite"
        restored.write_bytes(gzip.decompress(path.read_bytes()))
        with closing(sqlite3.connect(restored)) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM game_users").fetchone()[0], 1)
        self.assertGreater(admin_tools.database_size(self.db), 0)


class ChampionshipControlTests(Case):
    def teams(self, count):
        users = [str(1000 + n) for n in range(count)]
        for user in users:
            self.add_team(int(user))
        return users

    def test_time_parsing(self):
        self.assertEqual(parse_time("21"), "21:00")
        self.assertEqual(parse_time("21h30"), "21:30")
        self.assertEqual(parse_time("9:05"), "09:05")
        self.assertIsNone(parse_time("25:00"))
        self.assertIsNone(parse_time("tarde"))
        self.assertEqual(hour_label("19:00"), "19h")
        self.assertEqual(hour_label("20:30"), "20h30")

    def test_removed_team_loses_by_walkover_and_cancel_opens_next(self):
        users = self.teams(4)
        edition = league.start_edition(555, users, self.db, now=NOW)
        pending = league.remove_team(edition, users[0], self.db)
        self.assertEqual(pending, 3)
        with self.assertRaises(league.LeagueError):
            league.remove_team(edition, users[0], self.db)
        self.assertIn(users[0], league.lineup_problems(edition, self.db))
        for fixture in league.due_fixtures(edition, NOW, self.db):
            played = league.play_fixture(fixture, random.Random(1), self.db)
            if users[0] in (played["home_user_id"], played["away_user_id"]):
                self.assertIn(played["walkover"], {"mandante", "visitante"})
                self.assertNotEqual(played["winner_user_id"], users[0])
        nxt = league.cancel_edition(edition, self.db, now=NOW)
        self.assertEqual((nxt["number"], nxt["status"]), (2, "registration"))
        self.assertEqual(nxt["opens_on"], league.today(NOW).isoformat())  # pode começar já
        self.assertEqual(league.history(555, self.db), [])  # cancelada não tem campeão

    def test_shift_calendar_moves_every_day(self):
        users = self.teams(4)
        edition = league.start_edition(555, users, self.db, now=NOW)
        moved = league.shift_calendar(edition, 3, self.db)
        self.assertEqual(league.day_number(moved, NOW + timedelta(days=3)), 1)

    def test_scheduler_respects_time_pause_and_runs_once_a_day(self):
        cog = LeagueCog(SimpleNamespace(database_path=self.db, get_guild=lambda _id: guild))
        guild = SimpleNamespace(id=555)
        channel = SimpleNamespace(send=AsyncMock())
        cog.bot.get_channel = lambda _id: channel
        with connect(self.db) as connection:
            connection.execute(
                "INSERT INTO championship_settings (guild_id, channel_id, enabled, game_time) "
                "VALUES ('555', '9', 1, '21:00')"
            )
        runs = []

        async def fake_run(g, send, manual=False):
            runs.append(g.id)

        module = LeagueCog.daily_games.coro.__globals__
        with (
            patch.object(cog, "run_day", fake_run),
            patch.object(module["discord"], "TextChannel", SimpleNamespace),
        ):
            for hour, expected in ((20, 0), (21, 1), (22, 1)):  # antes, na hora, depois
                moment = datetime(2026, 11, 2, hour, 5, tzinfo=module["BRASILIA"])
                with patch.dict(module, {"_now": lambda: moment}):
                    asyncio.run(LeagueCog.daily_games.coro(cog))
                self.assertEqual(len(runs), expected, hour)
            with connect(self.db) as connection:
                connection.execute(
                    "UPDATE championship_settings SET last_run_on = NULL, paused_on = '2026-11-02'"
                )
            with patch.dict(module, {"_now": lambda: moment}):
                asyncio.run(LeagueCog.daily_games.coro(cog))
            self.assertEqual(len(runs), 1)  # pausado: não roda

    def test_pause_resume_time_status_and_cancel_commands(self):
        users = self.teams(4)
        cog = LeagueCog(SimpleNamespace(database_path=self.db))
        member = SimpleNamespace(bot=False)
        guild = SimpleNamespace(id=555, get_member=lambda _id: member)
        context = SimpleNamespace(
            author=SimpleNamespace(
                id=LURRAM, guild_permissions=SimpleNamespace(manage_guild=False)
            ),
            guild=guild,
            reply=AsyncMock(),
        )
        with connect(self.db) as connection:
            connection.execute(
                "INSERT INTO championship_settings (guild_id, channel_id, enabled) "
                "VALUES ('555', '9', 1)"
            )
        edition = league.start_edition(555, users, self.db, now=NOW)
        module = LeagueCog.pause.callback.__globals__["league"]
        clock = {"value": NOW}
        with patch.object(module, "today", lambda now=None: clock["value"].date()):
            asyncio.run(LeagueCog.pause.callback(cog, context))
            clock["value"] = NOW + timedelta(days=2)
            asyncio.run(LeagueCog.resume.callback(cog, context))
            moved = league.current_edition(555, self.db)
            self.assertEqual(league.day_number(moved, NOW + timedelta(days=2)), 1)  # sem atraso
            self.assertIn("2 dia(s)", context.reply.await_args.args[0])
            asyncio.run(LeagueCog.set_time.callback(cog, context, "21h30"))
            self.assertEqual(cog._settings(555)["game_time"], "21:30")
            asyncio.run(LeagueCog.status.callback(cog, context))
            status = context.reply.await_args.kwargs["embed"].description
            self.assertIn("21h30", status)
            self.assertIn("Edição **1**", status)
            asyncio.run(LeagueCog.cancel.callback(cog, context, None))
            self.assertIn("confirmar", context.reply.await_args.args[0])
            self.assertEqual(league.current_edition(555, self.db)["id"], edition["id"])
            asyncio.run(LeagueCog.cancel.callback(cog, context, "confirmar"))
            self.assertEqual(league.current_edition(555, self.db)["number"], 2)


if __name__ == "__main__":
    unittest.main()
