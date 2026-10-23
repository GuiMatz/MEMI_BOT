"""Calcula overalls de mercado sem substituir notas EA FC ou ajustes manuais."""

from __future__ import annotations

import argparse
import math
from pathlib import Path
from typing import Any

from src.db import DATABASE_PATH, connect, initialize_database

MIN_MARKET_VALUE_EUR = 5_000
MAX_MARKET_VALUE_EUR = 100_000_000
MIN_MARKET_OVERALL = 30
MAX_MARKET_OVERALL = 88


def estimate_overall(market_value_eur: int) -> int:
    """Estima overall somente pelo valor de mercado."""
    if market_value_eur < 0:
        raise ValueError("O valor de mercado não pode ser negativo.")
    bounded_value = min(max(market_value_eur, MIN_MARKET_VALUE_EUR), MAX_MARKET_VALUE_EUR)
    market_score = MIN_MARKET_OVERALL + (
        (MAX_MARKET_OVERALL - MIN_MARKET_OVERALL)
        * math.log(bounded_value / MIN_MARKET_VALUE_EUR)
        / math.log(MAX_MARKET_VALUE_EUR / MIN_MARKET_VALUE_EUR)
    )
    return max(1, min(99, round(market_score)))


def recalculate_overalls(
    database_path: str | Path = DATABASE_PATH,
    limit: int | None = None,
) -> dict[str, int]:
    """Aplica overrides manuais, preserva EA e estima os demais por mercado."""
    if limit is not None and limit < 1:
        raise ValueError("--limit deve ser um inteiro maior que zero.")
    initialize_database(database_path)
    updated = {"manual": 0, "ea_preserved": 0, "estimated": 0, "skipped": 0}
    query = """
        SELECT p.id, p.overall, p.overall_source, p.market_value_eur,
               oa.overall AS manual_overall
        FROM players AS p
        LEFT JOIN overall_adjustments AS oa ON oa.player_id = p.id
        ORDER BY p.id
    """
    if limit is not None:
        query += " LIMIT ?"

    with connect(database_path) as connection:
        rows = connection.execute(query, (limit,) if limit is not None else ()).fetchall()
        for row in rows:
            player_id = row["id"]
            if row["manual_overall"] is not None:
                connection.execute(
                    """
                    UPDATE players SET overall = ?, overall_source = 'manual',
                        overall_edition = NULL,
                        updated_at = CURRENT_TIMESTAMP WHERE id = ?
                    """,
                    (row["manual_overall"], player_id),
                )
                updated["manual"] += 1
            elif (
                row["overall"] is not None
                and row["overall_source"]
                and (
                    row["overall_source"].casefold() == "ea"
                    or row["overall_source"].casefold().startswith("ea_")
                )
            ):
                updated["ea_preserved"] += 1
            elif row["market_value_eur"] is not None:
                overall = estimate_overall(row["market_value_eur"])
                connection.execute(
                    """
                    UPDATE players SET overall = ?, overall_source = 'market_log',
                        overall_edition = NULL,
                        updated_at = CURRENT_TIMESTAMP WHERE id = ?
                    """,
                    (overall, player_id),
                )
                updated["estimated"] += 1
            else:
                updated["skipped"] += 1
    return updated


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DATABASE_PATH)
    parser.add_argument("--limit", type=int, help="recalcula no máximo N jogadores")
    args = parser.parse_args()
    try:
        result = recalculate_overalls(args.database, args.limit)
    except ValueError as error:
        parser.error(str(error))
    print("Overalls atualizados: " + ", ".join(f"{key}={value}" for key, value in result.items()))


if __name__ == "__main__":
    main()


def raw_for_game_overall(target: int) -> int:
    """Nota bruta que vira `target` na escala do jogo (abaixo de 90 a escala puxa para cima)."""
    from src.ratings import game_overall

    for raw in range(1, 100):
        if game_overall(raw) >= target:
            return raw
    return 99


def apply_current_stars(database_path: str | Path = DATABASE_PATH) -> dict[str, Any]:
    """Aplica as notas dos craques atuais (`src/seeds/craques_atuais.py`).

    Cada jogador é achado pelo nome (ou apelido); entre homônimos vale o de maior valor
    de mercado, que é o craque. Lendas (Messi, CR7…) ficam de fora: continuam 99 como
    lendas vivas. A nota vai para `overall_adjustments` (ajuste manual), então
    reimportações e recálculos não a desfazem.
    """
    from src.db import normalize_player_name
    from src.seeds.craques_atuais import CURRENT_STARS, STRETCH

    applied, missing, legends = 0, [], []
    with connect(database_path) as connection:
        rows = connection.execute(
            "SELECT id, name, display_name, overall_source, market_value_eur, overall "
            "FROM players"
        ).fetchall()
        by_name: dict[str, list] = {}
        for row in rows:
            for value in (row["name"], row["display_name"]):
                if value:
                    by_name.setdefault(normalize_player_name(value), []).append(row)
        for ea_rating, names in CURRENT_STARS:
            found = {
                int(row["id"]): row
                for name in names
                for row in by_name.get(normalize_player_name(name), [])
            }
            if not found:
                missing.append(names[0])
                continue
            best = max(
                found.values(),
                key=lambda row: (row["market_value_eur"] or 0, row["overall"] or 0),
            )
            source = (best["overall_source"] or "").casefold()
            if source in {"lenda", "lenda de clube", "cartola"}:
                legends.append(names[0])
                continue
            raw = raw_for_game_overall(min(99, ea_rating + STRETCH))
            connection.execute(
                """
                INSERT INTO overall_adjustments (player_id, overall, reason)
                VALUES (?, ?, 'craques atuais (EA FC 27)')
                ON CONFLICT(player_id) DO UPDATE SET
                    overall = excluded.overall, reason = excluded.reason,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (best["id"], raw),
            )
            connection.execute(
                "UPDATE players SET overall = ?, overall_source = 'manual', "
                "overall_edition = NULL, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (raw, best["id"]),
            )
            applied += 1
    return {"applied": applied, "missing": missing, "living_legends": legends}
