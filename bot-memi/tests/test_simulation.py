import random
import unittest

from src.simulation import (
    calculate_team_strength,
    expected_goals,
    penalty_shootout,
    simulate_match,
)
from src.teams import FORMATIONS


def make_team(prefix, overall):
    return [
        {
            "id": index + 1,
            "name": f"{prefix} {index + 1}",
            "display_name": f"{prefix} {index + 1}",
            "position": position,
            "overall": overall,
            "effective_overall": overall,
        }
        for index, position in enumerate(FORMATIONS["4-3-3"])
    ]


class SimulationTests(unittest.TestCase):
    def test_equal_lineups_start_at_equal_expected_goals(self):
        team = make_team("Igual", 80)
        strength = calculate_team_strength(team)
        result = simulate_match(
            "Time A",
            team,
            "Time B",
            team,
            rng=random.Random(11),
        )
        self.assertAlmostEqual(strength.attack, 80)
        self.assertAlmostEqual(strength.resistance, 80)
        self.assertEqual(result.home_xg, result.away_xg)
        self.assertAlmostEqual(result.home_xg, 1.31, delta=0.05)

    def test_expected_goals_respect_bounds_and_strength_difference(self):
        self.assertEqual(expected_goals(-100, 100), 0.2)
        self.assertEqual(expected_goals(100, -100), 3.5)
        self.assertGreater(expected_goals(85, 75), expected_goals(75, 85))

    def test_same_seed_reproduces_score_and_goal_events(self):
        home = make_team("Mandante", 82)
        away = make_team("Visitante", 78)
        first = simulate_match(
            "A",
            home,
            "B",
            away,
            home_style="attack",
            away_style="counter",
            rng=random.Random(2026),
        )
        second = simulate_match(
            "A",
            home,
            "B",
            away,
            home_style="attack",
            away_style="counter",
            rng=random.Random(2026),
        )
        self.assertEqual(first, second)
        self.assertEqual(
            len(first.goals),
            first.home_goals + first.away_goals,
        )
        self.assertTrue(all(1 <= goal.minute <= 90 for goal in first.goals))

    def test_better_lineup_wins_more_than_weaker_team_in_many_games(self):
        strong = make_team("Forte", 86)
        weak = make_team("Fraco", 70)
        rng = random.Random(22102026)
        strong_wins = weak_wins = draws = 0
        total_goals = 0
        for _ in range(3000):
            result = simulate_match(
                "Forte",
                strong,
                "Fraco",
                weak,
                rng=rng,
            )
            total_goals += result.home_goals + result.away_goals
            if result.home_goals > result.away_goals:
                strong_wins += 1
            elif result.home_goals < result.away_goals:
                weak_wins += 1
            else:
                draws += 1
        self.assertGreater(strong_wins, weak_wins)
        self.assertEqual(strong_wins + weak_wins + draws, 3000)
        average_goals = total_goals / 3000
        self.assertGreater(average_goals, 1.4)
        self.assertLess(average_goals, 4.0)

    def test_penalty_shootout_always_decides_tie(self):
        home, away = make_team("A", 80), make_team("B", 80)
        score = penalty_shootout(home, away, rng=random.Random(99))
        self.assertNotEqual(score[0], score[1])


if __name__ == "__main__":
    unittest.main()
