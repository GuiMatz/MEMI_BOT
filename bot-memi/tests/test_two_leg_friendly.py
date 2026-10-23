import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from src.cogs import matches
from src.cogs.matches import TWO_LEG_WORDS, MatchesCog


def result(home, away, home_goals, away_goals):
    return SimpleNamespace(
        home_name=home,
        away_name=away,
        home_goals=home_goals,
        away_goals=away_goals,
        events=[],
        goals=[],
    )


class TwoLegFriendlyTests(unittest.TestCase):
    def play(self, ida, volta, penalties=(0, 0)):
        """Joga ida e volta com placares fixos; devolve (eventos de missão, mensagens)."""
        cog = MatchesCog.__new__(MatchesCog)
        cog.database_path = ":memory:"
        teams = {100: "Desafiante FC", 200: "Rival FC"}
        context = SimpleNamespace(
            author=SimpleNamespace(id=100, display_name="Ana"), send=AsyncMock()
        )
        opponent = SimpleNamespace(id=200, display_name="Bia")
        events = MagicMock()
        kick = SimpleNamespace(home_score=penalties[0], away_score=penalties[1])
        with (
            patch.object(
                matches,
                "get_valid_lineup",
                side_effect=lambda user_id, _: ({"team_name": teams[user_id], "style": None}, []),
            ),
            patch.object(matches, "simulate_match", side_effect=[ida, volta]),
            patch.object(matches, "penalty_shootout_kicks", return_value=[kick]),
            patch.object(matches, "penalty_events", return_value=[]),
            patch.object(matches, "record_friendly") as record_friendly,
            patch.object(matches, "record_event", events),
            patch.object(matches, "narrate", AsyncMock()),
            patch.object(matches, "score_file", AsyncMock(return_value=None)),
            patch.object(matches, "venue_name", side_effect=lambda team: team["team_name"]),
        ):
            asyncio.run(cog._play_two_legs(context, opponent, with_income=False))
        homes = [call.args[0] for call in record_friendly.call_args_list]
        self.assertEqual(homes, [100, 200])  # ida na casa de quem desafiou, volta na do outro
        texts = [str(call.args[0]) for call in context.send.call_args_list if call.args]
        return events, texts

    def test_aggregate_decides_the_winner(self):
        # Ida 2x0 para o desafiante; volta 1x0 para o rival: agregado 2x1.
        events, texts = self.play(
            result("Desafiante FC", "Rival FC", 2, 0), result("Rival FC", "Desafiante FC", 1, 0)
        )
        events.assert_any_call(100, "friendly_win", ":memory:")
        self.assertTrue(any("Agregado:** Desafiante FC 2 × 1 Rival FC" in text for text in texts))
        self.assertEqual(sum(call.args[1] == "friendly" for call in events.call_args_list), 4)

    def test_tie_on_aggregate_goes_to_penalties_at_the_second_leg(self):
        # Ida 2x1, volta 1x0 para o rival: 2x2 no agregado; nos pênaltis da volta, o
        # desafiante (visitante) faz 4x3.
        events, texts = self.play(
            result("Desafiante FC", "Rival FC", 2, 1),
            result("Rival FC", "Desafiante FC", 1, 0),
            penalties=(3, 4),
        )
        events.assert_any_call(100, "friendly_win", ":memory:")
        self.assertTrue(any("pênaltis 4 a 3" in text and "Ana" in text for text in texts))

    def test_mode_words(self):
        for word in ("idaevolta", "ida", "iv", "2"):
            self.assertIn(word, TWO_LEG_WORDS)

    def test_options_with_wager(self):
        parse_friendly_options = matches.parse_friendly_options
        self.assertEqual(parse_friendly_options("idaevolta aposta 1.000"), (True, 1000, []))
        self.assertEqual(parse_friendly_options("ida e volta"), (True, 0, []))
        self.assertEqual(parse_friendly_options("aposta 5k"), (False, 5000, []))
        self.assertEqual(parse_friendly_options("xyz"), (False, 0, ["xyz"]))

    def test_wagered_match_pays_the_pot_or_refunds(self):
        # Usa o módulo importado no topo: o test_bot recarrega as extensões.
        cog = MatchesCog.__new__(MatchesCog)
        cog.database_path = ":memory:"
        context = SimpleNamespace(
            author=SimpleNamespace(id=100, display_name="Ana"), send=AsyncMock()
        )
        opponent = SimpleNamespace(id=200, display_name="Bia")
        for winner, expected in ((200, [(200, 2000)]), (None, [(100, 1000), (200, 1000)])):
            paid = MagicMock()
            with (
                patch.object(matches, "pay", paid),
                patch.object(MatchesCog, "_play_two_legs", AsyncMock(return_value=winner)),
            ):
                asyncio.run(cog.play_wagered(context, opponent, 1000, True))
            self.assertEqual([call.args[:2] for call in paid.call_args_list], expected)


if __name__ == "__main__":
    unittest.main()
