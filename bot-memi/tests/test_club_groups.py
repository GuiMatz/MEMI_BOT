import asyncio
import unittest
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

from src import club_groups, league
from src.club_groups import GroupError
from src.cogs.groups import GroupsCog, overview_embed, plan_embed
from src.db import connect
from src.game import claim_player
from src.teams import get_lineup
from tests.test_admin_tools import NOW, Case

MAIN, SECOND, THIRD = 1, 2, 3


class GroupCase(Case):
    def make_group(self, *accounts):
        club_groups.create_group(MAIN, "Grupo Lurram", self.db)
        for account in accounts:
            club_groups.invite(MAIN, account, self.db)
            club_groups.answer_invite(account, True, self.db)
        return club_groups.group_of(MAIN, self.db)

    def bench(self, owner, name, position="CA", overall=90):
        player = self.add_player(name, position, overall)
        claim_player(player, owner, self.db)
        return player

    def owner_of(self, player_id):
        with connect(self.db) as connection:
            return connection.execute(
                "SELECT discord_user_id FROM player_ownership WHERE player_id = ?", (player_id,)
            ).fetchone()[0]

    def starter_ids(self, user):
        return {int(p["id"]) for p in get_lineup(user, self.db)}


class MembershipTests(GroupCase):
    def test_invite_needs_accept_and_order_follows_joining(self):
        for user in (MAIN, SECOND, THIRD):
            self.add_team(user)
        club_groups.create_group(MAIN, "  Grupo   Lurram  ", self.db)
        club_groups.invite(MAIN, SECOND, self.db)
        self.assertIsNone(club_groups.group_of(SECOND, self.db))  # ainda não aceitou
        self.assertEqual(club_groups.pending_invite(SECOND, self.db)["name"], "Grupo Lurram")
        with self.assertRaises(GroupError):
            club_groups.invite(SECOND, THIRD, self.db)  # não é dono
        with self.assertRaises(GroupError):
            club_groups.invite(MAIN, SECOND, self.db)  # convite repetido
        club_groups.answer_invite(SECOND, True, self.db)
        club_groups.invite(MAIN, THIRD, self.db)
        club_groups.answer_invite(THIRD, False, self.db)
        self.assertIsNone(club_groups.pending_invite(THIRD, self.db))
        club_groups.invite(MAIN, THIRD, self.db)
        group = club_groups.answer_invite(THIRD, True, self.db)
        self.assertEqual(
            [(m["discord_user_id"], m["rank"]) for m in group["members"]],
            [("1", 1), ("2", 2), ("3", 3)],
        )
        group = club_groups.set_order(MAIN, [THIRD], self.db)
        self.assertEqual([m["discord_user_id"] for m in group["members"]], ["3", "1", "2"])
        with self.assertRaises(GroupError):
            club_groups.create_group(SECOND, "Outro", self.db)  # já está num grupo
        with self.assertRaises(GroupError):
            club_groups.leave(MAIN, self.db)  # o dono desfaz, não sai
        club_groups.leave(SECOND, self.db)
        group = club_groups.group_of(MAIN, self.db)
        self.assertEqual([m["rank"] for m in group["members"]], [1, 2])  # renumera
        club_groups.disband(MAIN, self.db)
        self.assertIsNone(club_groups.group_of(THIRD, self.db))

    def test_managed_account_only_for_owner(self):
        for user in (MAIN, SECOND):
            self.add_team(user)
        self.make_group(SECOND)
        self.assertEqual(club_groups.managed_account(MAIN, 2, self.db)["discord_user_id"], "2")
        self.assertEqual(club_groups.managed_account(SECOND, SECOND, self.db)["rank"], 2)
        with self.assertRaises(GroupError):
            club_groups.managed_account(SECOND, 1, self.db)
        self.assertEqual(club_groups.parse_rank("secundária"), 2)
        self.assertIsNone(club_groups.parse_rank("70"))


class OptimizeTests(GroupCase):
    def setUp(self):
        super().setUp()
        self.add_team(MAIN, rating=80)
        self.add_team(SECOND, rating=70)
        self.star = self.bench(SECOND, "Craque", "CA", 90)
        self.group = self.make_group(SECOND)
        self.main_striker = next(
            int(p["id"]) for p in get_lineup(MAIN, self.db) if p["position"] == "CA"
        )

    def test_best_goes_to_main_and_the_rest_cascades(self):
        plan = club_groups.optimize_plan(self.group, self.db, now=NOW)
        moved = {(int(m.player["id"]), m.to_id) for m in plan.moves}
        # O craque do banco da Secundária vai para a Principal; o CA 80 que sai do time da
        # Principal ainda é melhor que o CA 70 da Secundária, então vai para lá.
        self.assertEqual(moved, {(self.star, "1"), (self.main_striker, "2")})
        main, second = plan.accounts
        self.assertGreater(main.after, main.before)
        self.assertGreater(second.after, second.before)
        self.assertEqual(club_groups.apply_plan(MAIN, plan, self.db, now=NOW), 2)
        self.assertEqual(self.owner_of(self.star), "1")
        self.assertIn(self.star, self.starter_ids(MAIN))
        self.assertIn(self.main_striker, self.starter_ids(SECOND))
        self.assertEqual(len(self.starter_ids(SECOND)), 11)
        again = club_groups.optimize_plan(club_groups.group_of(MAIN, self.db), self.db, now=NOW)
        self.assertEqual(again.moves, [])  # estável
        embed = plan_embed(again, None)
        self.assertIn("Nada a mudar", embed.description)

    def test_only_owner_applies_and_stale_plan_is_refused(self):
        plan = club_groups.optimize_plan(self.group, self.db, now=NOW)
        with self.assertRaises(GroupError):
            club_groups.apply_plan(SECOND, plan, self.db, now=NOW)
        club_groups.move_player(MAIN, self.group, self.star, MAIN, self.db, now=NOW)
        with self.assertRaises(GroupError) as error:
            club_groups.apply_plan(MAIN, plan, self.db, now=NOW)
        self.assertIn("mudaram", str(error.exception))
        self.assertEqual(self.owner_of(self.main_striker), "1")  # nada pela metade

    def test_pack_locked_card_stays(self):
        with connect(self.db) as connection:
            connection.execute(
                "UPDATE player_ownership SET pack_origin = 1, pack_locked_until = ? "
                "WHERE player_id = ?",
                ((NOW + timedelta(hours=1)).isoformat(), self.star),
            )
        plan = club_groups.optimize_plan(self.group, self.db, now=NOW)
        self.assertNotIn(self.star, plan.moved_ids)
        with self.assertRaises(GroupError):
            club_groups.move_player(MAIN, self.group, self.star, MAIN, self.db, now=NOW)

    def test_group_sale_keeps_would_be_starters(self):
        junk = self.bench(SECOND, "Reserva Ruim", "ZAG", 50)
        plan = club_groups.optimize_plan(self.group, self.db, now=NOW)
        keep = {pid for a in plan.accounts for pid, _ in a.lineup.values()}
        ratings = {
            int(p["id"]): int(p["rating"]) for p in club_groups.group_players(self.group, self.db)
        }
        below = ratings[junk] + 1  # o overall de jogo não é o da carta
        found = club_groups.group_sale_ids(self.group, ["1", "2"], self.db, keep=keep, below=below)
        self.assertEqual(found, {"2": [junk]})
        # O craque está no banco da Secundária, mas seria titular na Principal: fica.
        found = club_groups.group_sale_ids(self.group, ["2"], self.db, keep=keep, below=100)
        self.assertNotIn(self.star, found.get("2", []))
        self.assertIn(
            self.star, club_groups.group_sale_ids(self.group, ["2"], self.db, below=100)["2"]
        )

    def test_roll_hint_points_to_the_account_above(self):
        newcomer = self.bench(SECOND, "Novato", "PE", 88)
        hint = club_groups.promotion_hint(SECOND, newcomer, True, self.db)
        self.assertEqual(hint["member"]["rank"], 1)
        self.assertEqual(hint["position"], "PE")
        weak = self.bench(MAIN, "Fraco", "PE", 60)
        self.assertIsNone(club_groups.promotion_hint(MAIN, weak, False, self.db))

    def test_overview_lists_idle_reinforcements(self):
        plan = club_groups.optimize_plan(self.group, self.db, now=NOW)
        embed = overview_embed(self.group, plan, self.db, None)
        text = " ".join(f.name + f.value for f in embed.fields)
        self.assertIn("Reforços parados (2)", text)
        self.assertIn("Craque", text)
        self.assertLessEqual(len(embed), 6000)


class LockTests(GroupCase):
    def setUp(self):
        super().setUp()
        self.users = [str(1000 + n) for n in range(4)]
        for user in self.users:
            self.add_team(int(user))
        self.edition = league.start_edition(555, self.users, self.db, now=NOW)
        with connect(self.db) as connection:
            connection.execute(
                "INSERT INTO championship_settings (guild_id, channel_id, enabled, game_time) "
                "VALUES ('555', '1', 1, '19:00')"
            )

    def test_locks_15_minutes_before_until_the_games_are_played(self):
        group = ["1000", "1001"]
        self.assertIsNone(
            club_groups.transfer_lock(group, self.db, now=NOW - timedelta(minutes=30))
        )
        message = club_groups.transfer_lock(group, self.db, now=NOW - timedelta(minutes=10))
        self.assertIn("19:00", message)
        self.assertIsNotNone(club_groups.transfer_lock(group, self.db, now=NOW))
        self.assertIsNone(club_groups.transfer_lock(["99"], self.db, now=NOW))  # não joga
        import random

        for game in league.due_fixtures(self.edition, NOW, self.db):
            league.play_fixture(game, random.Random(1), self.db)
        self.assertIsNone(club_groups.transfer_lock(group, self.db, now=NOW))
        with connect(self.db) as connection:
            connection.execute("UPDATE championship_settings SET paused_on = '2026-11-02'")
        tomorrow = NOW + timedelta(days=1)
        self.assertIsNone(club_groups.transfer_lock(group, self.db, now=tomorrow))  # pausado

    def test_move_is_refused_while_locked(self):
        club_groups.create_group(1000, "G", self.db)
        club_groups.invite(1000, 1001, self.db)
        club_groups.answer_invite(1001, True, self.db)
        group = club_groups.group_of(1000, self.db)
        player = self.bench(1001, "Alguém", "MC", 60)
        with self.assertRaises(GroupError) as error:
            club_groups.move_player(1000, group, player, 1000, self.db, now=NOW)
        self.assertIn("travadas", str(error.exception))
        club_groups.move_player(1000, group, player, 1000, self.db, now=NOW - timedelta(hours=2))
        self.assertEqual(self.owner_of(player), "1000")


class CommandTests(GroupCase):
    def context(self, user_id, mentions=()):
        return SimpleNamespace(
            author=SimpleNamespace(id=user_id, display_name=f"P{user_id}"),
            message=SimpleNamespace(mentions=list(mentions)),
            reply=AsyncMock(),
            typing=lambda: _NoTyping(),
        )

    def test_without_group_explains_and_shows_invite(self):
        self.add_team(MAIN)
        self.add_team(SECOND)
        cog = GroupsCog(SimpleNamespace(database_path=self.db))
        club_groups.create_group(MAIN, "Grupo Lurram", self.db)
        club_groups.invite(MAIN, SECOND, self.db)
        context = self.context(SECOND)
        asyncio.run(GroupsCog.group.callback(cog, context))
        text = context.reply.await_args.args[0]
        self.assertIn("mm!grupo criar", text)
        self.assertIn("Grupo Lurram", text)

    def test_move_by_rank_word(self):
        self.add_team(MAIN, rating=80)
        self.add_team(SECOND, rating=70)
        star = self.bench(SECOND, "Craque Raro", "CA", 90)
        self.make_group(SECOND)
        cog = GroupsCog(SimpleNamespace(database_path=self.db))
        context = self.context(MAIN)
        asyncio.run(GroupsCog.move.callback(cog, context, arguments="craque raro principal"))
        self.assertEqual(self.owner_of(star), "1")
        self.assertIn(star, self.starter_ids(MAIN))  # já entrou no time
        text = context.reply.await_args.kwargs["content"]
        self.assertIn("Principal", text)

    def test_quick_filter_target_parsing(self):
        self.add_team(MAIN)
        self.add_team(SECOND)
        group = self.make_group(SECOND)
        cog = GroupsCog(SimpleNamespace(database_path=self.db))
        target, rest = cog._target(self.context(MAIN), group, ["2", "abaixo", "70"])
        self.assertEqual((target["rank"], rest), (2, ["abaixo", "70"]))
        target, rest = cog._target(self.context(MAIN), group, ["abaixo", "70"])
        self.assertIsNone(target)


class _NoTyping:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


if __name__ == "__main__":
    unittest.main()
