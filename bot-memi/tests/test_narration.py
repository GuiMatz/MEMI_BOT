"""Testes da narração ao vivo: placar no topo, lances curtos e resumo final."""

import random
import unittest

from src.cogs.admin import _test_lineup
from src.narration import as_dicts, match_embed, team_dot
from src.simulation import penalty_events, penalty_shootout_kicks, simulate_match


class NarrationTests(unittest.TestCase):
    def setUp(self):
        self.home = _test_lineup(80, "Casa")
        self.away = _test_lineup(78, "Fora")
        self.result = simulate_match(
            "Memi FC", self.home, "Tricolor", self.away, rng=random.Random(3)
        )
        self.events = as_dicts(self.result.events)

    def test_team_dots_follow_the_team_color(self):
        self.assertEqual(team_dot("#C8102E"), "🔴")
        self.assertEqual(team_dot("#1E5AA8"), "🔵")
        self.assertEqual(team_dot("#2E8B57"), "🟢")
        self.assertEqual(team_dot("#333333"), "⚫")
        self.assertEqual(team_dot(None), "⚪")

    def test_events_carry_players(self):
        for event in self.events:
            if event["kind"] in {"goal", "save", "post", "miss", "card"}:
                self.assertTrue(event["player"], event)
            if event["kind"] == "save":
                self.assertTrue(event["detail"])

    def test_live_embed_has_scoreboard_and_short_recent_lines(self):
        embed = match_embed(
            "Memi FC", "Tricolor", self.events[:-1], home_color="#C8102E", away_color="#1E5AA8"
        )
        first_line = embed.description.splitlines()[0]
        self.assertTrue(first_line.startswith("### 🔴 Memi FC"))
        self.assertTrue(first_line.endswith("Tricolor 🔵"))
        self.assertNotIn("Cartão amarelo", embed.description)  # cartões só no resumo
        lines = embed.description.split("\n\n", 1)[1].splitlines()
        self.assertLessEqual(len(lines), 8)

    def test_final_embed_summarises_goals_cards_and_best_player(self):
        embed = match_embed("Memi FC", "Tricolor", self.events, finished=True)
        names = [field.name for field in embed.fields]
        self.assertTrue(any("Memi FC" in name for name in names))
        if self.result.goals:
            self.assertIn("⭐ Melhor em campo", names)

    def test_old_events_without_players_still_render(self):
        old = [dict(event, player=None, detail=None) for event in self.events]
        embed = match_embed("A", "B", old)
        self.assertTrue(embed.description)

    def test_penalties_show_kicker_and_running_score(self):
        kicks = penalty_shootout_kicks(self.home, self.away, rng=random.Random(1))
        timeline = self.events + penalty_events(kicks, "Memi FC", "Tricolor")
        embed = match_embed("Memi FC", "Tricolor", timeline)
        self.assertIn("Pênaltis", embed.description)
        self.assertIn(kicks[-1].kicker_name, embed.description)


if __name__ == "__main__":
    unittest.main()
