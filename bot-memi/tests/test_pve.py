import tempfile
import unittest
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from src.db import initialize_database
from src.game import get_wallet, register_user
from src.inventory import get_inventory
from src.pve import (
    DAILY_BOSS_PRIZE,
    WARMUP_TEAMS,
    WEEKLY_BOSS_TRIES,
    PveError,
    free_prize,
    ladder,
    ladder_prize,
    legend_teams,
    opponent_for,
    pve_status,
    record_result,
)
from src.simulation import simulate_match
from src.teams import FORMATIONS

NOW = datetime(2026, 10, 23, 15, tzinfo=timezone.utc)


class LegendTeamTests(unittest.TestCase):
    def test_every_legend_team_has_a_full_valid_eleven(self):
        teams = legend_teams()
        self.assertGreaterEqual(len(teams), 20)
        for team in teams:
            with self.subTest(team=team.name):
                self.assertEqual(len(team.players), 11)
                self.assertEqual(
                    Counter(p["position"] for p in team.players),
                    Counter(FORMATIONS[team.formation]),
                )
        self.assertEqual(teams[-1].key, "mundial")  # a mais forte fica no topo
        strengths = [team.strength for team in teams]
        self.assertEqual(strengths, sorted(strengths))

    def test_ladder_starts_easy_and_ends_with_the_world_team(self):
        tempdir = tempfile.TemporaryDirectory()
        database_path = Path(tempdir.name) / "pve.sqlite"
        initialize_database(database_path)
        steps = ladder(database_path)
        self.assertEqual(len(steps), len(WARMUP_TEAMS) + len(legend_teams()))
        self.assertLess(steps[0].strength, steps[len(WARMUP_TEAMS)].strength)
        self.assertEqual(steps[-1].key, "mundial")
        tempdir.cleanup()

    def test_bosses_get_more_strength_not_more_overall(self):
        import random

        from src.pve import DAILY_BOSS_BONUS, WEEKLY_BOSS_BONUS, daily_boss, weekly_boss

        boss = weekly_boss(NOW)
        base = next(team for team in legend_teams() if boss.key.startswith(team.key + ":"))
        self.assertEqual([p["overall"] for p in boss.players], [p["overall"] for p in base.players])
        self.assertEqual((boss.bonus, daily_boss(NOW).bonus), (WEEKLY_BOSS_BONUS, DAILY_BOSS_BONUS))
        tactics = boss.as_team()
        self.assertEqual(tactics["strength_bonus"], WEEKLY_BOSS_BONUS)
        self.assertIn(tactics["captain_player_id"], [p["id"] for p in boss.players])
        # O bônus pesa, sem deixar impossível.
        home = [
            {"id": i + 1, "name": f"U{i}", "position": p, "natural_position": p, "overall": 85}
            for i, p in enumerate(FORMATIONS["4-3-3"])
        ]
        rng = random.Random(3)
        results = [
            simulate_match(
                "U",
                home,
                boss.name,
                list(boss.players),
                away_tactics=tactics,
                home_advantage=True,
                rng=rng,
            )
            for _ in range(300)
        ]
        wins = sum(result.home_goals > result.away_goals for result in results)
        # Difícil, mas possível: um time 85 vence o chefão da semana de vez em quando.
        self.assertTrue(0.02 <= wins / 300 <= 0.2, wins)

    def test_a_legend_team_can_be_simulated(self):
        world = legend_teams()[-1]
        weaker = legend_teams()[0]
        result = simulate_match(weaker.name, list(weaker.players), world.name, list(world.players))
        self.assertGreaterEqual(result.home_goals + result.away_goals, 0)


class PveProgressTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.tempdir.name) / "pve.sqlite"
        initialize_database(self.database_path)
        register_user(100, "Ana", self.database_path)

    def tearDown(self):
        self.tempdir.cleanup()

    def play(self, kind, won, key=None):
        team = opponent_for(100, kind, key, self.database_path, NOW)
        return team, record_result(100, "Ana", kind, team, won, self.database_path, NOW)

    def test_ladder_advances_only_on_wins_and_pays_each_step_once(self):
        start = get_wallet(100, self.database_path)["memilets"]
        self.play("escada", False)
        self.assertEqual(pve_status(100, self.database_path, NOW).ladder_step, 0)
        for step in range(5):
            _, gains = self.play("escada", True)
            self.assertIn(f"{ladder_prize(step):,}".replace(",", "."), gains[0])
        self.assertEqual(pve_status(100, self.database_path, NOW).ladder_step, 5)
        paid = sum(ladder_prize(step) for step in range(5))
        self.assertEqual(get_wallet(100, self.database_path)["memilets"], start + paid)
        self.assertEqual(get_inventory(100, self.database_path), {"caixa": 1})  # 5º degrau

    def test_daily_boss_has_one_try_and_weekly_boss_three(self):
        _, gains = self.play("dia", True)
        self.assertIn(f"{DAILY_BOSS_PRIZE:,}".replace(",", "."), gains[0])
        self.assertEqual(get_inventory(100, self.database_path), {"caixa_epica": 1})
        with self.assertRaises(PveError):
            self.play("dia", True)
        for _ in range(WEEKLY_BOSS_TRIES):
            self.play("semana", False)
        with self.assertRaises(PveError):
            self.play("semana", True)

    def test_free_challenge_pays_the_first_win_per_team_per_day(self):
        key = legend_teams()[0].key
        team, gains = self.play("livre", True, key)
        self.assertIn(f"{free_prize(team):,}".replace(",", "."), gains[0])
        _, again = self.play("livre", True, key)
        self.assertEqual(again, [])
        other = legend_teams()[1].key
        _, gains = self.play("livre", True, other)
        self.assertTrue(gains)
        with self.assertRaises(PveError):
            opponent_for(100, "livre", "nao-existe", self.database_path, NOW)


class CampaignAndAchievementTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.tempdir.name) / "pve.sqlite"
        initialize_database(self.database_path)
        register_user(100, "Ana", self.database_path)

    def tearDown(self):
        self.tempdir.cleanup()

    def play(self, kind, won):
        team = opponent_for(100, kind, None, self.database_path, NOW)
        return team, record_result(100, "Ana", kind, team, won, self.database_path, NOW)

    def test_copa_eliminates_on_a_loss_and_pays_the_champion(self):
        from src.pve import CAMPAIGNS, campaign_progress, pve_title

        copa = CAMPAIGNS["copa"]
        first, _ = self.play("campanha:copa", True)
        self.assertEqual(first.name, "Uruguai Lendária")
        self.assertEqual(
            pve_title(100, "campanha:copa", self.database_path),
            "Copa das Lendas · Quartas de final",
        )
        _, gains = self.play("campanha:copa", False)
        self.assertIn("recomeça", gains[0])
        self.assertEqual(campaign_progress(100, self.database_path)["copa"], (0, 0))
        start = get_wallet(100, self.database_path)["memilets"]
        for _ in range(len(copa.teams())):
            team, gains = self.play("campanha:copa", True)
        self.assertEqual(team.name, "Brasil Lendária")  # a final
        self.assertTrue(any("Copa das Lendas completa" in gain for gain in gains))
        self.assertTrue(any("Campeão da Copa" in gain for gain in gains))  # conquista
        self.assertEqual(campaign_progress(100, self.database_path)["copa"], (0, 1))
        self.assertGreaterEqual(get_wallet(100, self.database_path)["memilets"] - start, copa.prize)
        self.assertEqual(get_inventory(100, self.database_path).get("pacote_epico"), 1)

    def test_other_campaigns_retry_the_same_match_after_a_loss(self):
        from src.pve import campaign_progress

        self.play("campanha:brasileirao", True)
        self.play("campanha:brasileirao", False)
        self.assertEqual(campaign_progress(100, self.database_path)["brasileirao"], (1, 0))

    def test_achievements_are_paid_once(self):
        from src.pve import earned_achievements

        _, gains = self.play("dia", True)
        self.assertTrue(any("Chefe do dia" in gain for gain in gains))
        self.assertTrue(any("Primeira lenda" in gain for gain in gains))
        self.assertEqual(
            earned_achievements(100, self.database_path), {"chefao_dia", "primeira_lenda"}
        )
        team = legend_teams()[0]
        again = record_result(100, "Ana", "livre", team, True, self.database_path, NOW)
        self.assertFalse(any("Conquista" in gain for gain in again))


class PveHubTests(unittest.IsolatedAsyncioTestCase):
    async def test_hub_shows_ladder_bosses_and_free_challenge(self):
        from types import SimpleNamespace

        from src.cogs.pve_views import PveHubView, hub_embed

        tempdir = tempfile.TemporaryDirectory()
        database_path = Path(tempdir.name) / "pve.sqlite"
        initialize_database(database_path)
        register_user(100, "Ana", database_path)
        user = SimpleNamespace(id=100, display_name="Ana")
        embed = hub_embed(user, database_path, 80)
        names = [field.name for field in embed.fields]
        self.assertEqual(
            names, ["🪜 Escada", "☀️ Chefão do dia", "🗓️ Chefão da semana", "⚔️ Desafio livre"]
        )
        self.assertIn("força **80**", embed.description)
        view = PveHubView(None, user, database_path)
        labels = [getattr(item, "label", None) for item in view.children]
        self.assertEqual(
            labels[:5], ["Escada", "Chefão do dia", "Chefão da semana", "Campanhas", "Conquistas"]
        )
        self.assertEqual(len(view.children[5].options), len(legend_teams()))
        from src.cogs.pve_views import CampaignView, achievements_embed, campaigns_embed
        from src.pve import ACHIEVEMENTS, CAMPAIGNS

        self.assertEqual(len(campaigns_embed(user, database_path).fields), len(CAMPAIGNS))
        self.assertEqual(
            achievements_embed(user, database_path).title, f"🏅 Conquistas · 0/{len(ACHIEVEMENTS)}"
        )
        self.assertEqual(len(CampaignView(None, user).children), len(CAMPAIGNS))
        tempdir.cleanup()


class PveMatchFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_clicking_ladder_plays_a_narrated_match_and_records_it(self):
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, patch

        from src.cogs import matches
        from src.db import connect
        from src.game import claim_player
        from src.teams import ensure_team, scale_player

        tempdir = tempfile.TemporaryDirectory()
        database_path = Path(tempdir.name) / "pve.sqlite"
        initialize_database(database_path)
        register_user(100, "Ana", database_path)
        ensure_team(100, "Ana", database_path)
        for index, position in enumerate(FORMATIONS["4-3-3"]):
            with connect(database_path) as connection:
                player_id = connection.execute(
                    "INSERT INTO players (name, display_name, game_position, overall, "
                    "activity_status, phase2_candidate) VALUES (?, ?, ?, 80, 'active', 0)",
                    (f"J{index}", f"J{index}", position),
                ).lastrowid
            claim_player(player_id, 100, database_path)
            scale_player(100, player_id, database_path)
        cog = matches.MatchesCog.__new__(matches.MatchesCog)
        cog.database_path = database_path
        channel = SimpleNamespace(send=AsyncMock())
        interaction = SimpleNamespace(
            user=SimpleNamespace(id=100, display_name="Ana"),
            response=SimpleNamespace(send_message=AsyncMock()),
            channel=channel,
        )
        with (
            patch.object(matches, "narrate", AsyncMock()),
            patch.object(matches, "score_file", AsyncMock(return_value=None)),
        ):
            await cog.start_pve(interaction, "escada", None)
        self.assertIn("Escada", interaction.response.send_message.call_args.args[0])
        final = channel.send.call_args.args[0]
        self.assertTrue("venceu" in final or "Empate" in final)
        with connect(database_path) as connection:
            played = connection.execute("SELECT COUNT(*) FROM pve_log").fetchone()[0]
        self.assertEqual(played, 1)
        tempdir.cleanup()


if __name__ == "__main__":
    unittest.main()
