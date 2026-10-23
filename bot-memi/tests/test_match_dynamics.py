import random
import unittest
from collections import Counter
from dataclasses import asdict

from src.narration import _line, match_embed
from src.simulation import simulate_match
from src.teams import FORMATIONS


def make_team(prefix, overall=80, first_id=1):
    return [
        {
            "id": first_id + index,
            "name": f"{prefix} {index + 1}",
            "position": position,
            "effective_overall": overall,
        }
        for index, position in enumerate(FORMATIONS["4-3-3"])
    ]


HOME, AWAY = make_team("Casa"), make_team("Fora", first_id=100)


def play_many(games, seed=3, **options):
    rng = random.Random(seed)
    return [simulate_match("A", HOME, "B", AWAY, rng=rng, **options) for _ in range(games)]


class MatchDynamicsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.results = play_many(1500, home_advantage=True)

    def test_home_advantage_helps_the_home_side(self):
        neutral = play_many(1500)

        def balance(results):
            return sum(result.home_goals - result.away_goals for result in results) / len(results)

        self.assertGreater(balance(self.results), 0.1)
        self.assertGreater(balance(self.results), balance(neutral) + 0.1)

    def test_the_game_heats_up_at_the_end(self):
        minutes = [goal.minute for result in self.results for goal in result.goals]
        early = sum(minute <= 15 for minute in minutes)
        late = sum(minute >= 76 for minute in minutes)
        self.assertGreater(late, early * 1.3)

    def test_a_red_card_hurts_the_team_that_gets_it(self):
        # Amostra própria e maior: expulsão no 1º tempo é rara, e com poucos jogos o
        # resultado oscila demais.
        results = play_many(4000, seed=99, home_advantage=True)
        overall = sum(r.home_goals - r.away_goals for r in results) / len(results)
        scored_with_ten, games = 0, 0
        for result in results:
            reds = [event for event in result.events if event.kind == "red"]
            if reds and reds[0].side == "home" and reds[0].minute <= 45:
                games += 1
                scored_with_ten += result.home_goals - result.away_goals
        self.assertGreater(games, 30)
        self.assertLess(scored_with_ten / games, overall - 0.3)

    def test_sent_off_player_does_not_appear_again(self):
        for result in self.results:
            reds = {
                event.player: index
                for index, event in enumerate(result.events)
                if event.kind == "red"
            }
            for name, index in reds.items():
                later = [
                    event
                    for event in result.events[index + 1 :]
                    if event.player == name and event.kind != "red"
                ]
                self.assertFalse(later, name)

    def test_matches_have_varied_events(self):
        kinds = Counter(event.kind for result in self.results for event in result.events)
        plays = Counter(goal.play for result in self.results for goal in result.goals)
        for kind in ("goal", "save", "miracle", "post", "miss", "red", "offside", "pressure"):
            self.assertGreater(kinds[kind], 0, kind)
        for play in (
            None,
            "penalti",
            "falta",
            "cabeca",
            "fora",
            "escanteio",
            "rebote",
            "contra_ataque",
            "contra",
        ):
            self.assertGreater(plays[play], 0, play)
        # Futebol real: cerca de uma expulsão a cada 10 jogos.
        self.assertLess(kinds["red"] / len(self.results), 0.15)

    def test_own_goal_is_credited_to_an_opponent_defender(self):
        home_ids = {player["id"] for player in HOME}
        own_goals = [
            goal for result in self.results for goal in result.goals if goal.play == "contra"
        ]
        self.assertTrue(own_goals)
        for goal in own_goals:
            self.assertEqual(goal.scorer_id in home_ids, goal.side == "away")

    def test_full_time_stats_are_consistent(self):
        for result in self.results[:200]:
            stats = result.stats
            self.assertEqual(sum(stats["possession"]), 100)
            for side in (0, 1):
                self.assertLessEqual(stats["on_target"][side], stats["shots"][side])
            goals = (result.home_goals, result.away_goals)
            own = Counter(goal.side for goal in result.goals if goal.play == "contra")
            self.assertGreaterEqual(stats["on_target"][0], goals[0] - own["home"])
            ratings = [value for side in stats["ratings"].values() for value in side.values()]
            self.assertEqual(len(ratings), 22)
            self.assertEqual(stats["best"]["rating"], max(ratings))
            self.assertTrue(all(3 <= value <= 10 for value in ratings))
            self.assertEqual(result.events[-1].stats, stats)


class DynamicNarrationTests(unittest.TestCase):
    dots = {"home": "🔴", "away": "🔵"}

    def event(self, kind, **extra):
        data = {
            "minute": 30,
            "stoppage": 0,
            "kind": kind,
            "text": "texto",
            "side": "home",
            "home_goals": 1,
            "away_goals": 0,
            "player": "Fulano",
            "detail": None,
        }
        return {**data, **extra}

    def test_live_lines_for_new_events(self):
        self.assertIn("GOL CONTRA", _line(self.event("goal", play="contra"), self.dots))
        self.assertIn("de cabeça", _line(self.event("goal", play="cabeca"), self.dots))
        self.assertIn("⏱️", _line(self.event("goal", minute=90, stoppage=3), self.dots))
        self.assertIn(
            "Milagre de Goleiro", _line(self.event("miracle", detail="Goleiro"), self.dots)
        )
        self.assertIn("expulso", _line(self.event("red"), self.dots))
        self.assertIn("impedido", _line(self.event("offside"), self.dots))
        self.assertIn("encurrala", _line(self.event("pressure", text="🔥 encurrala"), self.dots))

    def test_final_embed_shows_stats_and_best_rating(self):
        result = play_many(1, seed=11)[0]
        events = [asdict(event) for event in result.events]
        embed = match_embed("A", "B", events, finished=True)
        fields = {field.name: field.value for field in embed.fields}
        self.assertIn("Posse", fields["📊 Estatísticas"])
        self.assertIn(f"{result.stats['best']['rating']:.1f}", fields["⭐ Melhor em campo"])

    def test_old_saved_matches_without_stats_still_render(self):
        events = [self.event("goal"), self.event("fulltime", side=None, player=None)]
        embed = match_embed("A", "B", events, finished=True)
        names = [field.name for field in embed.fields]
        self.assertNotIn("📊 Estatísticas", names)
        self.assertIn("⭐ Melhor em campo", names)


if __name__ == "__main__":
    unittest.main()
