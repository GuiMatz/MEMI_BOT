"""Overall de jogo e séries de raridade do FUT.AE.

O banco guarda a nota original das fontes (EA FC, mercado, ajustes manuais).
O jogo usa uma escala valorizada calculada aqui, sem reescrever o dado bruto:
reimportações continuam funcionando e a curva pode ser ajustada sem migração.
"""

from __future__ import annotations

import re
import unicodedata

# Notas abaixo do teto sobem proporcionalmente à distância até ele; o topo fica
# igual. Ex.: 45 → 63, 60 → 72, 75 → 81, 85 → 87, 90+ sem mudança.
BOOST_CEILING = 90
BOOST_FACTOR = 0.4
CARTOLA_OVERALL = 90
SPECIAL_OVERALL = 100
MAX_REGULAR_OVERALL = 99
# A única carta 100 do jogo; comparada com o nome normalizado.
SPECIAL_PLAYER_NAMES = frozenset({"maria sofia"})

# Séries de raridade em ordem crescente: (rótulo, emoji, menor, maior, peso no roll).
RARITY_TIERS: tuple[tuple[str, str, int, int, float], ...] = (
    ("Base", "⚪", 1, 59, 51.85),
    ("Comum", "🟢", 60, 69, 30.0),
    ("Incomum", "🔵", 70, 79, 15.0),
    ("Raro", "🟣", 80, 86, 3.0),
    ("Épico", "🟠", 87, 89, 0.1),
    ("Lendário", "🔴", 90, 94, 0.04),
    ("Ícone", "🌟", 95, 97, 0.008),
    ("Galáctico", "💫", 98, 99, 0.0015),
    ("Única", "👑", 100, 100, 0.0005),
)


def _normalize(value: str | None) -> str:
    if not value:
        return ""
    text = unicodedata.normalize("NFKD", value)
    text = "".join(character for character in text if not unicodedata.combining(character))
    return re.sub(r"[^a-zA-Z0-9]+", " ", text).casefold().strip()


def is_special_player(name: str | None) -> bool:
    return _normalize(name) in SPECIAL_PLAYER_NAMES


def game_overall(
    raw_overall: int | None,
    overall_source: str | None = None,
    name: str | None = None,
) -> int | None:
    """Converte a nota bruta da fonte no overall usado em todo o jogo."""
    if raw_overall is None:
        return None
    if is_special_player(name):
        return SPECIAL_OVERALL
    if (overall_source or "").casefold() == "cartola":
        return CARTOLA_OVERALL
    raw = int(raw_overall)
    if raw < BOOST_CEILING:
        raw = round(raw + (BOOST_CEILING - raw) * BOOST_FACTOR)
    return max(1, min(MAX_REGULAR_OVERALL, raw))


def effective_overall(overall: int | None, balls: int | None = 0) -> int | None:
    """Overall com bolas de duplicata; só a carta especial chega a 100."""
    if overall is None:
        return None
    cap = SPECIAL_OVERALL if int(overall) >= SPECIAL_OVERALL else MAX_REGULAR_OVERALL
    return min(cap, int(overall) + int(balls or 0))


def rarity_index(overall: int | None) -> int | None:
    if overall is None:
        return None
    for index, (_, _, lower, upper, _) in enumerate(RARITY_TIERS):
        if lower <= overall <= upper:
            return index
    raise ValueError(f"Overall fora das séries: {overall}")


def rarity_label(overall: int | None) -> str:
    """Rótulo curto com emoji, por exemplo `🟣 Raro`."""
    if overall is None:
        return "—"
    label, emoji, *_ = RARITY_TIERS[rarity_index(int(overall))]
    return f"{emoji} {label}"


def register_sql_functions(connection) -> None:
    """Disponibiliza as regras acima dentro das consultas SQLite."""
    connection.create_function("game_overall", 3, game_overall, deterministic=True)
    connection.create_function("effective_overall", 2, effective_overall, deterministic=True)
    connection.create_function("rarity_index", 1, rarity_index, deterministic=True)


# Expressões reutilizadas nas consultas (alias `p` para players e `b` para bolas).
GAME_OVERALL_SQL = "game_overall(p.overall, p.overall_source, p.name)"
EFFECTIVE_OVERALL_SQL = f"effective_overall({GAME_OVERALL_SQL}, COALESCE(b.balls, 0))"
