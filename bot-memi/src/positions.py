"""Quem pode jogar onde: rendimento de cada posição natural fora dela.

O rendimento multiplica o overall do jogador na vaga. Um ponta-esquerda 90 jogando de
ponta-direita rende 90 × 0,90 = 81: entra no lugar de um PD 75, mas não de um PD 82.
Posições que não aparecem na tabela de um jogador não são permitidas (o goleiro só joga
no gol, e ninguém de linha vai para o gol).

A tabela segue a proximidade de função em campo: o mesmo papel do outro lado rende mais
que trocar de setor; laterais e pontas do mesmo lado se cobrem; o meia transita entre
volante, pontas e centroavante.
"""

from __future__ import annotations

FIT: dict[str, dict[str, float]] = {
    "GOL": {"GOL": 1.0},
    "ZAG": {"ZAG": 1.0, "LD": 0.85, "LE": 0.85, "MC": 0.85},
    "LD": {"LD": 1.0, "LE": 0.9, "ZAG": 0.85, "PD": 0.85, "MC": 0.8},
    "LE": {"LE": 1.0, "LD": 0.9, "ZAG": 0.85, "PE": 0.85, "MC": 0.8},
    "MC": {"MC": 1.0, "MEI": 0.9, "ZAG": 0.85, "LD": 0.8, "LE": 0.8},
    "MEI": {"MEI": 1.0, "MC": 0.9, "PE": 0.85, "PD": 0.85, "CA": 0.85},
    "PE": {"PE": 1.0, "PD": 0.9, "MEI": 0.85, "CA": 0.85, "LE": 0.8},
    "PD": {"PD": 1.0, "PE": 0.9, "MEI": 0.85, "CA": 0.85, "LD": 0.8},
    "CA": {"CA": 1.0, "PE": 0.85, "PD": 0.85, "MEI": 0.8},
}


def fit(natural: str | None, slot: str) -> float:
    """Rendimento (0 a 1) de quem é `natural` jogando na vaga `slot`; 0 = não pode."""
    if not natural:
        return 0.0
    return FIT.get(natural, {}).get(slot, 0.0)


def slot_rating(overall: int | float, natural: str | None, slot: str) -> int:
    """Overall que o jogador rende na vaga (arredondado); 0 se não pode jogar lá."""
    return round(float(overall) * fit(natural, slot))


def compatible_naturals(slot: str) -> list[str]:
    """Posições naturais que podem ocupar a vaga, da mais adequada para a menos."""
    return sorted(
        (natural for natural, slots in FIT.items() if slot in slots),
        key=lambda natural: -FIT[natural][slot],
    )
