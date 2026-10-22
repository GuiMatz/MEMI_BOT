import random
import unittest

from src.positions import FIT, compatible_naturals, fit, slot_rating
from src.simulation import simulate_match, team_profile
from src.teams import (
    FORMATIONS,
    TeamError,
    auto_fill_lineup,
    best_assignment,
    ensure_team,
    get_lineup,
    set_lineup_slot,
)
from tests.test_futae_melhorias import DatabaseCase
from src.game import claim_player


def make_team(prefix, overall=80, formation="4-3-3", **by_position):
    return [
        {
            "id": index + 1,
            "name": f"{prefix} {index + 1}",
            "position": position,
            "natural_position": position,
            "effective_overall": by_position.get(position, overall),
        }
        for index, position in enumerate(FORMATIONS[formation])
    ]


def play_many(home, away, games=1500, seed=7, **options):
    rng = random.Random(seed)
    results = [simulate_match("A", home, "B", away, rng=rng, **options) for _ in range(games)]
    scored = sum(result.home_goals for result in results) / games
    conceded = sum(result.away_goals for result in results) / games
    return scored, conceded, results


class PositionFitTests(unittest.TestCase):
    def test_goalkeeper_only_plays_in_goal(self):
        self.assertEqual(FIT["GOL"], {"GOL": 1.0})
        self.assertEqual(compatible_naturals("GOL"), ["GOL"])
        self.assertTrue(all("GOL" not in FIT[natural] for natural in FIT if natural != "GOL"))

    def test_mirrored_winger_keeps_most_of_the_rating(self):
        self.assertEqual(fit("PE", "PD"), 0.9)
        self.assertEqual(slot_rating(90, "PE", "PD"), 81)
        self.assertEqual(fit("CA", "ZAG"), 0)
        self.assertEqual(compatible_naturals("PD")[0], "PD")
        self.assertTrue(
            all(0.8 <= value <= 1 for slots in FIT.values() for value in slots.values())
        )


class AssignmentTests(unittest.TestCase):
    def test_best_assignment_maximizes_total_and_respects_forbidden(self):
        self.assertEqual(best_assignment([[10, None, 5], [9, 8, None]]), [(0, 0), (1, 1)])
        self.assertEqual(best_assignment([[None, None], [3, 4]]), [(1, 1)])
        # O primeiro vale mais na vaga 1, mas o total é maior com ele na vaga 0.
        self.assertEqual(best_assignment([[9, 10], [1, 8]]), [(0, 0), (1, 1)])


class CompatibleLineupTests(DatabaseCase):
    def setUp(self):
        super().setUp()
        ensure_team(100, "Pessoa", self.database_path)

    def own(self, position, overall=60, name=None):
        player_id = self.add_player(name or f"{position} {overall}", position, overall)
        self.assertTrue(claim_player(player_id, 100, self.database_path))
        return player_id

    def test_much_better_left_winger_takes_the_right_wing(self):
        for position in FORMATIONS["4-3-3"]:
            if position != "PD":
                self.own(position, 70)
        right = self.own("PD", 50, "Ponta direita fraco")
        star = self.own("PE", 88, "Ponta esquerda craque")
        auto_fill_lineup(100, self.database_path)
        lineup = {player["id"]: player for player in get_lineup(100, self.database_path)}
        self.assertEqual(lineup[star]["position"], "PE")
        self.assertNotIn(right, lineup)
        # O PE que perdeu a vaga para o craque vai para a direita, rendendo 90%.
        (right_wing,) = [player for player in lineup.values() if player["position"] == "PD"]
        self.assertEqual(right_wing["natural_position"], "PE")
        self.assertEqual(
            right_wing["slot_overall"], slot_rating(right_wing["effective_overall"], "PE", "PD")
        )

    def test_slightly_better_winger_does_not_take_a_natural_spot(self):
        for position in FORMATIONS["4-3-3"]:
            if position != "PD":
                self.own(position, 60)
        right = self.own("PD", 70, "Ponta direita")
        self.own("PE", 75, "Ponta esquerda")  # renderia 68 de PD
        auto_fill_lineup(100, self.database_path)
        lineup = {player["id"]: player for player in get_lineup(100, self.database_path)}
        self.assertEqual(lineup[right]["position"], "PD")

    def test_auto_fill_covers_missing_positions_with_compatible_players(self):
        for position in ("GOL", "ZAG", "ZAG", "ZAG", "LE", "MC", "MC", "MEI", "PE", "CA", "CA"):
            self.own(position, 70)
        self.assertEqual(auto_fill_lineup(100, self.database_path), 11)
        self.assertEqual(len(get_lineup(100, self.database_path)), 11)

    def test_manual_slot_accepts_compatible_and_rejects_incompatible(self):
        winger = self.own("PE", 90)
        striker = self.own("CA", 90)
        right_wing = FORMATIONS["4-3-3"].index("PD") + 1
        set_lineup_slot(100, right_wing, winger, self.database_path)
        (player,) = get_lineup(100, self.database_path)
        self.assertEqual((player["natural_position"], player["slot_overall"]), ("PE", 81))
        with self.assertRaises(TeamError):
            set_lineup_slot(100, 1, striker, self.database_path)


class PlayerDrivenSimulationTests(unittest.TestCase):
    def test_bad_goalkeeper_concedes_more(self):
        base = make_team("Base")
        _, conceded_good, _ = play_many(make_team("Bom", GOL=90), base)
        _, conceded_bad, _ = play_many(make_team("Ruim", GOL=55), base)
        self.assertGreater(conceded_bad, conceded_good * 1.5)

    def test_strong_defense_weak_attack_makes_low_scoring_games(self):
        base = make_team("Base")
        _, _, mirror = play_many(base, make_team("Igual"))
        defensive = make_team(
            "Retranca", GOL=88, ZAG=88, LD=88, LE=88, MC=85, MEI=65, PE=65, PD=65, CA=65
        )
        scored, conceded, _ = play_many(defensive, base)
        average = sum(result.home_goals for result in mirror) / len(mirror)
        self.assertLess(scored, average * 0.9)
        self.assertLess(conceded, average * 0.9)

    def test_better_striker_scores_a_bigger_share(self):
        def striker_share(rating):
            team = make_team("Time", CA=rating)
            _, _, results = play_many(team, make_team("Rival"), games=800)
            goals = [goal for result in results for goal in result.goals if goal.side == "home"]
            striker = next(player["id"] for player in team if player["position"] == "CA")
            return sum(goal.scorer_id == striker for goal in goals) / len(goals)

        self.assertGreater(striker_share(92), striker_share(70) + 0.1)

    def test_attacking_style_opens_the_game_and_defensive_formation_closes_it(self):
        base = make_team("Base")
        open_for, open_against, _ = play_many(make_team("A"), base, home_style="attack")
        shut_for, shut_against, _ = play_many(make_team("A", formation="5-4-1"), base)
        self.assertGreater(open_for + open_against, shut_for + shut_against + 0.3)

    def test_chosen_takers_take_the_set_pieces(self):
        home = make_team("Casa")
        taker = home[2]["id"]  # um zagueiro
        tactics = {"penalty_player_id": taker, "free_kick_player_id": taker}
        _, _, results = play_many(home, make_team("Fora"), games=600, home_tactics=tactics)
        set_pieces = [
            event
            for result in results
            for event in result.events
            if event.side == "home" and event.play in {"falta", "penalti"}
        ]
        self.assertTrue(set_pieces)
        self.assertTrue(all(event.player == "Casa 3" for event in set_pieces))

    def test_off_position_player_counts_less(self):
        natural = make_team("Natural")
        shifted = make_team("Trocado")
        winger = next(player for player in shifted if player["position"] == "PD")
        winger["natural_position"] = "PE"
        self.assertLess(team_profile(shifted).attack, team_profile(natural).attack)


if __name__ == "__main__":
    unittest.main()
