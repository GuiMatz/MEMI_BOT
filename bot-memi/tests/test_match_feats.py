import random
import unittest
from dataclasses import asdict

from src.narration import _line, match_embed
from src.score_image import _goals_by_side, feat_label
from src.simulation import simulate_match
from src.teams import FORMATIONS


def make_team(prefix, overall, first_id=1):
    return [
        {"id": first_id + i, "name": f"{prefix}{i}", "position": p, "effective_overall": overall}
        for i, p in enumerate(FORMATIONS["4-3-3"])
    ]


class MatchFeatTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        rng = random.Random(8)
        strong, weak = make_team("F", 88), make_team("W", 70, 100)
        cls.results = [simulate_match("A", strong, "B", weak, rng=rng) for _ in range(1200)]

    def test_hat_trick_event_comes_exactly_with_the_third_goal(self):
        seen = 0
        for result in self.results:
            goals: dict[tuple[str, str], int] = {}
            for event in result.events:
                if event.kind == "goal" and event.play != "contra":
                    key = (event.side, event.player)
                    goals[key] = goals.get(key, 0) + 1
                if event.kind == "feat" and event.play == "hattrick":
                    seen += 1
                    self.assertGreaterEqual(goals[(event.side, event.player)], 3)
                    self.assertIn(event.player, event.text)
        self.assertGreater(seen, 5)

    def test_comeback_only_when_the_team_was_really_behind(self):
        seen = 0
        for result in self.results:
            behind = {"home": False, "away": False}
            last = (0, 0)
            for event in result.events:
                if event.kind == "goal":
                    last = (event.home_goals, event.away_goals)
                    if last[0] < last[1]:
                        behind["home"] = True
                    if last[1] < last[0]:
                        behind["away"] = True
                if event.kind == "feat" and event.play == "virada":
                    seen += 1
                    ahead = last[0] > last[1] if event.side == "home" else last[1] > last[0]
                    self.assertTrue(ahead and behind[event.side])
        self.assertGreater(seen, 10)

    def test_highlights_reach_the_final_summary(self):
        result = next(r for r in self.results if any(e.kind == "feat" for e in r.events))
        events = [asdict(event) for event in result.events]
        fields = {f.name: f.value for f in match_embed("A", "B", events, finished=True).fields}
        self.assertEqual(fields["🏅 Destaques"], "\n".join(result.stats["highlights"]))
        feat = next(e for e in events if e["kind"] == "feat")
        self.assertIn(feat["text"], _line(feat, {"home": "🔴", "away": "🔵"}))


class ScorerGroupingTests(unittest.TestCase):
    def test_goals_of_the_same_player_are_grouped(self):
        goals = [
            {"minute": 6, "side": "home", "scorer_name": "Eusébio"},
            {"minute": 22, "side": "home", "scorer_name": "Eusébio"},
            {"minute": 40, "side": "home", "scorer_name": "Rossi"},
            {"minute": 90, "stoppage": 2, "side": "home", "scorer_name": "Eusébio"},
            {"minute": 50, "side": "home", "scorer_name": "Zagueiro", "play": "contra"},
        ]
        rows = _goals_by_side(goals, "home")
        self.assertEqual([row["name"] for row in rows], ["Eusébio", "Rossi", "Zagueiro (contra)"])
        self.assertEqual(rows[0]["minutes"], ["6'", "22'", "90'+2"])
        self.assertEqual(rows[0]["count"], 3)

    def test_feat_labels(self):
        self.assertIsNone(feat_label(2))
        self.assertEqual(feat_label(3), "HAT-TRICK")
        self.assertEqual(feat_label(4), "POKER")
        self.assertEqual(feat_label(7), "MANITA")


if __name__ == "__main__":
    unittest.main()
