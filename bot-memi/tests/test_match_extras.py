import random
import unittest
from dataclasses import asdict

from src.commentary import PHRASES, _situation, match_commentary, star_lines
from src.narration import _line, match_embed
from src.pve import LEGEND_STYLES, legend_teams
from src.score_image import _kick_rows
from src.simulation import STYLES, PenaltyKick, simulate_match
from tests.test_match_dynamics import AWAY, HOME, make_team

DOTS = {"home": "🔴", "away": "🔵"}


def event(kind, minute=10, side="home", home=0, away=0, **extra):
    data = {"minute": minute, "stoppage": 0, "kind": kind, "text": "", "side": side}
    return {**data, "home_goals": home, "away_goals": away, **extra}


def goal(minute, side, home, away, player="Fulano", **extra):
    return event("goal", minute, side, home, away, player=player, **extra)


def fulltime(home, away, stats=None):
    return event("fulltime", 90, None, home, away, stats=stats)


class LegendStyleTests(unittest.TestCase):
    def test_every_legend_has_a_style_and_bosses_keep_it(self):
        teams = legend_teams()
        self.assertEqual({team.key for team in teams}, set(LEGEND_STYLES))
        self.assertTrue(set(LEGEND_STYLES.values()) <= set(STYLES))
        italy = next(team for team in teams if team.key == "selecao:itália")
        boss = italy.as_boss(0.01, "2026-W43")
        self.assertEqual(boss.style, "park")
        self.assertEqual(boss.as_team()["style"], "park")
        self.assertIn("retranca", boss.power)


class LateTacticTests(unittest.TestCase):
    def test_team_losing_at_70_goes_all_out_and_says_so(self):
        rng = random.Random(1)
        switched = 0
        for _ in range(300):
            result = simulate_match("A", HOME, "B", AWAY, rng=rng)
            for item in result.events:
                if item.kind != "tactic":
                    continue
                switched += 1
                self.assertEqual(item.minute, 70)
                losing = item.home_goals < item.away_goals
                self.assertEqual(item.side, "home" if losing else "away")
                self.assertIn("ataque total", _line(asdict(item), DOTS))
        self.assertGreater(switched, 50)


class MatchPenaltyTests(unittest.TestCase):
    def test_penalties_in_the_match_say_how_they_went(self):
        rng = random.Random(4)
        shots = []
        for _ in range(800):
            result = simulate_match("A", HOME, "B", AWAY, rng=rng)
            shots += [item for item in result.events if item.play == "penalti"]
        self.assertTrue(all(item.shot for item in shots))
        missed = [item for item in shots if item.kind != "goal"]
        saved = sum(item.kind in {"save", "miracle"} for item in missed) / len(missed)
        self.assertGreater(saved, 0.6)  # pênalti perdido é quase sempre defesa
        line = _line(asdict(next(item for item in shots if item.kind == "goal")), DOTS)
        self.assertIn("GOL DE PÊNALTI", line)
        line = _line(asdict(missed[0]), DOTS)
        self.assertIn("Pênalti perdido", line)


class KickRowTests(unittest.TestCase):
    def test_kicks_are_split_by_team_in_order(self):
        kicks = [
            PenaltyKick("home", "A", True, 1, 0),
            {"side": "away", "kicker_name": "B", "scored": False},
            PenaltyKick("home", "C", False, 1, 0),
        ]
        self.assertEqual(_kick_rows(kicks), {"home": [True, False], "away": [False]})
        self.assertEqual(_kick_rows(None), {"home": [], "away": []})


class CommentaryTests(unittest.TestCase):
    def test_situations(self):
        cases = {
            "zero_a_zero": [fulltime(0, 0)],
            "virada": [
                goal(10, "away", 0, 1),
                goal(50, "home", 1, 1),
                goal(80, "home", 2, 1),
                fulltime(2, 1),
            ],
            "virada_insana": [
                goal(10, "away", 0, 1),
                goal(20, "away", 0, 2),
                goal(50, "home", 1, 2),
                goal(60, "home", 2, 2),
                goal(80, "home", 3, 2),
                fulltime(3, 2),
            ],
            "hattrick": [goal(m, "home", i + 1, 0, "Zé") for i, m in enumerate((5, 30, 60))]
            + [fulltime(3, 0)],
            "goleada": [goal(10 * i, "home", i, 0, f"P{i}") for i in range(1, 6)]
            + [fulltime(5, 0)],
            "gol_no_fim": [goal(89, "away", 0, 1), fulltime(0, 1)],
            "penaltis": [fulltime(1, 1), event("penalty", 90, "away", 3, 4)],
        }
        for expected, events in cases.items():
            with self.subTest(expected):
                self.assertEqual(_situation(events)[0], expected)
                text = match_commentary(events, "Casa", "Fora")
                self.assertTrue(text)
                self.assertNotIn("{", text)

    def test_every_phrase_fills_its_blanks(self):
        values = dict.fromkeys(
            ("winner", "loser", "team", "other", "home", "away", "player", "goals", "keeper"), "X"
        )
        for phrases in PHRASES.values():
            for phrase in phrases:
                phrase.format(score="1 × 0", **values)

    def test_same_match_same_phrase_and_it_shows_in_the_final_embed(self):
        events = [goal(10, "home", 1, 0), goal(50, "home", 2, 0), fulltime(2, 0)]
        first = match_commentary(events, "Casa", "Fora")
        self.assertEqual(first, match_commentary(events, "Casa", "Fora"))
        embed = match_embed("Casa", "Fora", events, finished=True)
        fields = {field.name: field.value for field in embed.fields}
        self.assertIn(first, fields["🎙️ Análise"])

    def test_star_players_get_their_line(self):
        events = [
            goal(10, "home", 1, 0, "Cristiano Ronaldo"),
            goal(20, "home", 2, 0, "Cristiano Ronaldo"),
            goal(30, "away", 2, 1, "Abédi Pelé"),
            goal(40, "away", 2, 2, "Lionel Messi"),
            fulltime(2, 2),
        ]
        lines = star_lines(events)
        self.assertEqual(len(lines), 2)
        self.assertIn("Cristiano", lines[0])
        self.assertIn("Messi", lines[1])

    def test_real_matches_always_get_a_commentary(self):
        rng = random.Random(9)
        for strength in (70, 80, 90):
            home = make_team("Casa", strength)
            for _ in range(60):
                result = simulate_match("A", home, "B", AWAY, rng=rng)
                self.assertTrue(match_commentary([asdict(e) for e in result.events], "A", "B"))


if __name__ == "__main__":
    unittest.main()
