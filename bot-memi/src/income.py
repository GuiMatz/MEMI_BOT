"""Renda do amistoso (estádio) e bilheteria do campeonato (andar 5 da torre).

Renda: quem tem estádio equipado e joga um amistoso em casa ganha a renda do primeiro
amistoso do dia. O intervalo é sempre o do daily base (24 h); nenhum upgrade o reduz.
Sem estádio, o time joga no "Centro de Treinamento" e não ganha renda.

Bilheteria: cada jogo do campeonato diário guarda 50 Memilets (100 na vitória) numa
reserva com limite, coletada com `mm!bilheteria`.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from src.db import DATABASE_PATH, connect
from src.perks import BASE_DAILY_COOLDOWN, INCOME_BASE, load_perks

INCOME_COOLDOWN = BASE_DAILY_COOLDOWN
INCOME_PER_GOAL = 50
INCOME_WIN = 100
INCOME_LOSS = -100
# Perder por 3 gols ou mais sem marcar: a torcida vai embora e a renda zera.
WALKOUT_MARGIN = 3
BOX_OFFICE_PER_GAME = 50
BOX_OFFICE_PER_WIN = 100


class IncomeError(Exception):
    """Regra da renda que pode ser mostrada ao jogador."""


def _utc_now(now: datetime | None) -> datetime:
    value = now or datetime.now(timezone.utc)
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("now deve incluir um fuso horário.")
    return value.astimezone(timezone.utc)


def venue_name(team: dict) -> str:
    return team.get("stadium_name") or f"Centro de Treinamento do {team['team_name']}"


@dataclass(frozen=True)
class IncomeStatus:
    stadium: str | None
    available_at: datetime | None
    total: int
    bonus: int

    @property
    def ready(self) -> bool:
        return bool(self.stadium) and self.available_at is None


def income_status(
    discord_user_id: int | str,
    database_path: str | Path = DATABASE_PATH,
    *,
    now: datetime | None = None,
) -> IncomeStatus:
    current_time = _utc_now(now)
    user_id = str(discord_user_id)
    with connect(database_path) as connection:
        account = connection.execute(
            "SELECT last_income_at, income_total FROM game_users WHERE discord_user_id = ?",
            (user_id,),
        ).fetchone()
        team = connection.execute(
            "SELECT stadium_name FROM game_teams WHERE discord_user_id = ?", (user_id,)
        ).fetchone()
        bonus = load_perks(connection, user_id).income_bonus
    available_at = None
    if account and account["last_income_at"]:
        available_at = datetime.fromisoformat(account["last_income_at"]) + INCOME_COOLDOWN
        if available_at <= current_time:
            available_at = None
    return IncomeStatus(
        stadium=team["stadium_name"] if team else None,
        available_at=available_at,
        total=int(account["income_total"]) if account else 0,
        bonus=bonus,
    )


def calculate_income(goals_for: int, goals_against: int, bonus: int = 0) -> tuple[int, str]:
    """Renda e o humor da torcida: `adorou`, `reembolso` ou `saiu`."""
    if goals_for == 0 and goals_against - goals_for >= WALKOUT_MARGIN:
        return 0, "saiu"
    amount = INCOME_BASE + bonus + INCOME_PER_GOAL * goals_for
    if goals_for > goals_against:
        amount += INCOME_WIN
    elif goals_for < goals_against:
        amount += INCOME_LOSS
    amount = max(0, amount)
    return amount, ("adorou" if amount >= INCOME_BASE + bonus else "reembolso")


INCOME_MESSAGES = {
    "adorou": "📣 Torcida adorou esse amistoso — renda: **{amount}**",
    "reembolso": "😤 Torcedor pediu reembolso — renda: **{amount}**",
    "saiu": "🚪 A torcida saiu do estádio antes do fim — renda: **{amount}**",
}


@dataclass(frozen=True)
class IncomeResult:
    amount: int
    mood: str
    available_at: datetime

    @property
    def message(self) -> str:
        return INCOME_MESSAGES[self.mood].format(amount=f"{self.amount:,}".replace(",", "."))


def collect_income(
    discord_user_id: int | str,
    goals_for: int,
    goals_against: int,
    database_path: str | Path = DATABASE_PATH,
    *,
    now: datetime | None = None,
) -> IncomeResult:
    """Credita a renda do amistoso em casa; só funciona uma vez a cada 24 h."""
    current_time = _utc_now(now)
    user_id = str(discord_user_id)
    with connect(database_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        team = connection.execute(
            "SELECT stadium_name FROM game_teams WHERE discord_user_id = ?", (user_id,)
        ).fetchone()
        if team is None or not team["stadium_name"]:
            raise IncomeError("Sem estádio equipado o amistoso não gera renda.")
        account = connection.execute(
            "SELECT last_income_at FROM game_users WHERE discord_user_id = ?", (user_id,)
        ).fetchone()
        if account["last_income_at"]:
            available_at = datetime.fromisoformat(account["last_income_at"]) + INCOME_COOLDOWN
            if current_time < available_at:
                raise IncomeError("A renda de hoje já foi recebida.")
        bonus = load_perks(connection, user_id).income_bonus
        amount, mood = calculate_income(goals_for, goals_against, bonus)
        connection.execute(
            """
            UPDATE game_users
            SET memilets = memilets + ?, income_total = income_total + ?,
                last_income_at = ?, updated_at = CURRENT_TIMESTAMP
            WHERE discord_user_id = ?
            """,
            (amount, amount, current_time.isoformat(timespec="seconds"), user_id),
        )
        return IncomeResult(amount, mood, current_time + INCOME_COOLDOWN)


def add_box_office(connection: sqlite3.Connection, discord_user_id: str, won: bool) -> int:
    """Guarda a bilheteria de um jogo oficial; devolve quanto entrou na reserva."""
    perks = load_perks(connection, discord_user_id)
    if not perks.floor(5):
        return 0
    row = connection.execute(
        "SELECT box_office FROM game_users WHERE discord_user_id = ?", (discord_user_id,)
    ).fetchone()
    reserve = int(row["box_office"]) if row else 0
    earned = (BOX_OFFICE_PER_WIN if won else BOX_OFFICE_PER_GAME) * perks.floor(5)
    credited = max(0, min(earned, perks.box_office_cap - reserve))
    if credited:
        connection.execute(
            "UPDATE game_users SET box_office = box_office + ? WHERE discord_user_id = ?",
            (credited, discord_user_id),
        )
    return credited


@dataclass(frozen=True)
class BoxOffice:
    reserve: int
    cap: int
    total: int
    active: bool


def box_office_status(
    discord_user_id: int | str, database_path: str | Path = DATABASE_PATH
) -> BoxOffice:
    user_id = str(discord_user_id)
    with connect(database_path) as connection:
        perks = load_perks(connection, user_id)
        row = connection.execute(
            "SELECT box_office, box_office_total FROM game_users WHERE discord_user_id = ?",
            (user_id,),
        ).fetchone()
    return BoxOffice(
        int(row["box_office"]) if row else 0,
        perks.box_office_cap,
        int(row["box_office_total"]) if row else 0,
        bool(perks.floor(5)),
    )


def collect_box_office(
    discord_user_id: int | str, database_path: str | Path = DATABASE_PATH
) -> int:
    user_id = str(discord_user_id)
    with connect(database_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT box_office FROM game_users WHERE discord_user_id = ?", (user_id,)
        ).fetchone()
        amount = int(row["box_office"]) if row else 0
        if amount:
            connection.execute(
                """
                UPDATE game_users
                SET memilets = memilets + box_office,
                    box_office_total = box_office_total + box_office,
                    box_office = 0, updated_at = CURRENT_TIMESTAMP
                WHERE discord_user_id = ?
                """,
                (user_id,),
            )
    return amount
