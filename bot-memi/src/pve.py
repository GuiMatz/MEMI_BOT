"""Desafios contra times lendários (`mm!desafio`): escada, desafio livre e chefões.

Os adversários não são de ninguém: são montados na hora a partir das lendas da
enciclopédia (seleções por país e a Seleção Mundial), dos 11 lendários de cada grande
clube brasileiro e, nos primeiros degraus da escada, de jogadores do mercado num overall
alvo. Cada time joga na formação em que seus jogadores rendem mais.

- **Escada**: degraus do mais fácil ao mais difícil; vencer libera o próximo e paga o
  prêmio do degrau (só na primeira vez), com uma caixa a cada 5 degraus.
- **Desafio livre**: qualquer time lendário; o prêmio sai na primeira vitória do dia
  contra cada time.
- **Chefão do dia** (1 tentativa) e **chefão da semana** (3 tentativas): um time
  lendário reforçado, com prêmio grande.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

from src.clock import BRASILIA
from src.db import DATABASE_PATH, connect
from src.encyclopedia import CLUB_LEGEND_POSITIONS, load_club_legends, load_encyclopedia
from src.encyclopedia import legend_overall
from src.game import register_user
from src.inventory import give
from src.positions import fit, slot_rating
from src.ratings import GAME_OVERALL_SQL, POOL_SQL, game_overall
from src.teams import FORMATIONS, best_assignment

# Degraus fáceis do começo da escada: (nome, overall alvo).
WARMUP_TEAMS = (
    ("Várzea FC", 62),
    ("Amadores do Bairro", 67),
    ("Reservas Unidos", 72),
    ("Seleção Sub-20", 76),
    ("Combinado Estadual", 79),
)
# Seleções lendárias: países com lendas suficientes para um time inteiro.
NATION_EMOJIS = {
    "Brasil": "🇧🇷",
    "Itália": "🇮🇹",
    "Inglaterra": "🏴",
    "Alemanha": "🇩🇪",
    "França": "🇫🇷",
    "Argentina": "🇦🇷",
    "Espanha": "🇪🇸",
    "Holanda": "🇳🇱",
    "Portugal": "🇵🇹",
    "Uruguai": "🇺🇾",
}
# Força acima do overall (multiplica ataque, defesa, meio e goleiro na simulação). O
# overall das lendas não muda. Um time de força 85 vence: clube ~32%, seleção ~12%,
# Seleção Mundial ~7%, chefão do dia ~26% (clube) ou ~10% (seleção), chefão da semana
# ~6-9% por tentativa.
CLUB_BONUS = 0.02
NATION_BONUS = 0.02
WORLD_BONUS = 0.03
DAILY_BOSS_BONUS = 0.04
WEEKLY_BOSS_BONUS = 0.05
IMPROVISED = 0.6  # peso, na escolha, de quem jogaria fora de posição (só se não houver outro)
DAILY_BOSS_TRIES = 1
WEEKLY_BOSS_TRIES = 3
DAILY_BOSS_PRIZE = 3_000
WEEKLY_BOSS_PRIZE = 12_000


class PveError(Exception):
    """Desafio recusado (tentativas, degrau); a mensagem vai para a pessoa."""


@dataclass(frozen=True)
class PveTeam:
    key: str
    name: str
    emoji: str
    formation: str
    players: tuple[dict[str, Any], ...]
    bonus: float = 0.0  # força acima do overall (ataque, defesa, meio e goleiro)

    @property
    def strength(self) -> int:
        """Média do quanto os 11 rendem nas vagas (o "overall do time")."""
        return round(
            sum(
                slot_rating(p["overall"], p["natural_position"], p["position"])
                for p in self.players
            )
            / len(self.players)
        )

    @property
    def label(self) -> str:
        return f"{self.emoji} {self.name}"

    @property
    def power(self) -> str:
        """Força para mostrar: o overall do time e o bônus de lenda ("94 +10%")."""
        return f"{self.strength}" + (f" +{round(self.bonus * 100)}%" if self.bonus else "")

    def as_team(self) -> dict[str, Any]:
        """O dicionário de time que a simulação e o placar esperam (com capitão e bônus)."""
        captain = max(self.players, key=lambda player: player["overall"])
        return {
            "team_name": self.name,
            "style": "balanced",
            "formation": self.formation,
            "captain_player_id": captain["id"],
            "strength_bonus": self.bonus,
        }

    def as_boss(self, bonus: float, suffix: str) -> "PveTeam":
        """O mesmo time como chefão: mesmo overall, mais força."""
        return PveTeam(
            f"{self.key}:{suffix}", self.name, self.emoji, self.formation, self.players, bonus
        )


def _best_eleven(candidates: list[dict[str, Any]], id_start: int) -> tuple[str, list[dict]]:
    """A formação e os 11 que rendem mais (cada um na vaga em que mais rende).

    Se nenhuma formação fecha com as posições compatíveis (um clube com um zagueiro só),
    alguém joga improvisado: assume a vaga com 15% a menos de overall."""
    if len(candidates) < 11:
        raise ValueError("Não dá para montar 11 com esses jogadores.")

    def score(candidate, slot) -> float:
        if fit(candidate["natural"], slot):
            return slot_rating(candidate["overall"], candidate["natural"], slot)
        return candidate["overall"] * IMPROVISED  # improvisado: última opção

    best = None
    for formation, slots in FORMATIONS.items():
        scores = [[score(c, slot) for c in candidates] for slot in slots]
        pairs = best_assignment(scores)
        total = sum(scores[row][column] for row, column in pairs)
        if best is None or total > best[0]:
            best = (total, formation, pairs)
    _, formation, pairs = best
    slots = FORMATIONS[formation]
    players = []
    for row, column in sorted(pairs):
        chosen = candidates[column]
        natural, overall = chosen["natural"], chosen["overall"]
        if not fit(natural, slots[row]):
            natural, overall = slots[row], round(overall * 0.85)
        players.append(
            {
                "id": id_start - row,
                "name": chosen["name"],
                "display_name": chosen["name"],
                "position": slots[row],
                "natural_position": natural,
                "overall": overall,
                "effective_overall": overall,
            }
        )
    return formation, players


@lru_cache(maxsize=1)
def legend_teams() -> tuple[PveTeam, ...]:
    """Clubes brasileiros lendários, seleções lendárias e a Seleção Mundial."""
    teams = []
    for index, club in enumerate(load_club_legends()):
        candidates = [
            {
                "name": player["name"],
                "natural": CLUB_LEGEND_POSITIONS.get(player["position"], player["position"]),
                "overall": game_overall(int(player["overall"])),
            }
            for player in club["players"]
        ]
        formation, players = _best_eleven(candidates, -1000 * (index + 1))
        key = "clube:" + club["club"].lower()
        teams.append(
            PveTeam(key, f"{club['club']} Lendário", "🛡️", formation, tuple(players), CLUB_BONUS)
        )
    legends = load_encyclopedia()["legends"]
    for index, (country, emoji) in enumerate(NATION_EMOJIS.items()):
        candidates = [
            {
                "name": legend["name"],
                "natural": legend["position"],
                "overall": legend_overall(legend["name"]),
            }
            for legend in legends
            if legend["country"] == country
        ]
        try:
            formation, players = _best_eleven(candidates, -50_000 - 1000 * index)
        except ValueError:
            continue
        teams.append(
            PveTeam(
                "selecao:" + country.lower(),
                f"{country} Lendária",
                emoji,
                formation,
                tuple(players),
                NATION_BONUS,
            )
        )
    everyone = [
        {
            "name": legend["name"],
            "natural": legend["position"],
            "overall": legend_overall(legend["name"]),
        }
        for legend in legends
    ]
    # Só os melhores de cada posição entram na conta (o resultado é o mesmo e fica rápido).
    top = sorted(everyone, key=lambda c: -c["overall"])[:120]
    formation, players = _best_eleven(top, -90_000)
    teams.append(
        PveTeam("mundial", "Seleção Mundial", "🌍", formation, tuple(players), WORLD_BONUS)
    )
    return tuple(sorted(teams, key=lambda team: team.strength))


def warmup_team(name: str, target: int, database_path: str | Path = DATABASE_PATH) -> PveTeam:
    """Degrau fácil: jogadores do mercado perto de `target` (sempre os mesmos para o nome)."""
    rng = random.Random(name)
    slots = FORMATIONS["4-3-3"]
    players = []
    with connect(database_path) as connection:
        for row, slot in enumerate(slots):
            options = connection.execute(
                f"""
                SELECT COALESCE(NULLIF(p.display_name, ''), p.name) AS name, p.game_position,
                       {GAME_OVERALL_SQL} AS overall
                FROM players AS p
                WHERE {POOL_SQL} AND p.game_position = ? AND {GAME_OVERALL_SQL} BETWEEN ? AND ?
                ORDER BY p.id LIMIT 40
                """,
                (slot, target - 3, target + 3),
            ).fetchall()
            if options:
                chosen = rng.choice(options)
                name_, overall = chosen["name"], int(chosen["overall"])
            else:
                name_, overall = f"Reserva ({slot})", target
            players.append(
                {
                    "id": -100 - row,
                    "name": name_,
                    "display_name": name_,
                    "position": slot,
                    "natural_position": slot,
                    "overall": overall,
                    "effective_overall": overall,
                }
            )
    return PveTeam("treino:" + name.lower(), name, "⚽", "4-3-3", tuple(players))


def ladder(database_path: str | Path = DATABASE_PATH) -> list[PveTeam]:
    """Os degraus da escada, do mais fácil ao mais difícil."""
    warmups = [warmup_team(name, target, database_path) for name, target in WARMUP_TEAMS]
    return warmups + list(legend_teams())


def ladder_prize(step: int) -> int:
    """Memilets do degrau `step` (0 = o primeiro)."""
    return 400 + 200 * step


def free_prize(team: PveTeam) -> int:
    return max(300, (team.strength - 70) * 60)


def today(now: datetime | None = None) -> str:
    return (now or datetime.now(timezone.utc)).astimezone(BRASILIA).date().isoformat()


def this_week(now: datetime | None = None) -> str:
    year, week, _ = (now or datetime.now(timezone.utc)).astimezone(BRASILIA).isocalendar()
    return f"{year}-W{week:02d}"


def _pick(teams: list[PveTeam], seed: str) -> PveTeam:
    return teams[random.Random(seed).randrange(len(teams))]


def daily_boss(now: datetime | None = None) -> PveTeam:
    """Um time lendário por dia (clube ou seleção), um pouco reforçado."""
    day = today(now)
    team = _pick([t for t in legend_teams() if t.key != "mundial"], "dia:" + day)
    return team.as_boss(DAILY_BOSS_BONUS, day)


def weekly_boss(now: datetime | None = None) -> PveTeam:
    """Uma seleção lendária (ou a Mundial) por semana, mais reforçada."""
    week = this_week(now)
    teams = [t for t in legend_teams() if t.key.startswith("selecao:") or t.key == "mundial"]
    return _pick(teams, "semana:" + week).as_boss(WEEKLY_BOSS_BONUS, week)


@dataclass(frozen=True)
class PveStatus:
    ladder_step: int  # degraus já vencidos
    ladder_total: int
    daily_tries: int
    daily_won: bool
    weekly_tries: int
    weekly_won: bool


def _count(connection, user_id: str, kind: str, period: str, won: bool | None = None) -> int:
    query = "SELECT COUNT(*) FROM pve_log WHERE discord_user_id = ? AND kind = ? AND period = ?"
    arguments: list[Any] = [user_id, kind, period]
    if won is not None:
        query += " AND won = ?"
        arguments.append(int(won))
    return connection.execute(query, arguments).fetchone()[0]


def pve_status(
    discord_user_id: int | str,
    database_path: str | Path = DATABASE_PATH,
    now: datetime | None = None,
) -> PveStatus:
    user_id = str(discord_user_id)
    with connect(database_path) as connection:
        row = connection.execute(
            "SELECT ladder FROM pve_progress WHERE discord_user_id = ?", (user_id,)
        ).fetchone()
        day, week = today(now), this_week(now)
        return PveStatus(
            int(row["ladder"]) if row else 0,
            len(WARMUP_TEAMS) + len(legend_teams()),
            _count(connection, user_id, "dia", day),
            bool(_count(connection, user_id, "dia", day, True)),
            _count(connection, user_id, "semana", week),
            bool(_count(connection, user_id, "semana", week, True)),
        )


def opponent_for(
    discord_user_id: int | str,
    kind: str,
    team_key: str | None = None,
    database_path: str | Path = DATABASE_PATH,
    now: datetime | None = None,
) -> PveTeam:
    """O adversário de um desafio, se ainda dá para jogar (senão, `PveError`)."""
    status = pve_status(discord_user_id, database_path, now)
    if kind == "escada":
        if status.ladder_step >= status.ladder_total:
            raise PveError("Você já venceu a escada inteira! 🏆 Desafie os chefões.")
        return ladder(database_path)[status.ladder_step]
    if kind == "dia":
        if status.daily_won or status.daily_tries >= DAILY_BOSS_TRIES:
            raise PveError("O chefão de hoje já foi enfrentado. Volta amanhã!")
        return daily_boss(now)
    if kind == "semana":
        if status.weekly_won:
            raise PveError("Você já derrubou o chefão desta semana!")
        if status.weekly_tries >= WEEKLY_BOSS_TRIES:
            raise PveError("Suas tentativas contra o chefão da semana acabaram.")
        return weekly_boss(now)
    if kind == "livre":
        for team in legend_teams():
            if team.key == team_key:
                return team
        raise PveError("Não achei esse time lendário.")
    if kind.startswith("campanha:"):
        campaign = CAMPAIGNS.get(kind.split(":", 1)[1])
        if campaign is None:
            raise PveError("Campanha desconhecida.")
        with connect(database_path) as connection:
            step, _ = campaign_step(connection, str(discord_user_id), campaign.key)
        return campaign.teams()[step]
    raise PveError("Modo de desafio desconhecido.")


def record_result(
    discord_user_id: int | str,
    display_name: str,
    kind: str,
    team: PveTeam,
    won: bool,
    database_path: str | Path = DATABASE_PATH,
    now: datetime | None = None,
) -> list[str]:
    """Guarda o resultado, avança a escada e paga os prêmios. Devolve o que a pessoa ganhou."""
    user_id = str(discord_user_id)
    register_user(user_id, display_name, database_path)
    period = this_week(now) if kind == "semana" else today(now)
    gains: list[str] = []
    with connect(database_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        already = 0
        if kind == "livre":
            already = connection.execute(
                "SELECT COUNT(*) FROM pve_log WHERE discord_user_id = ? AND kind = 'livre' "
                "AND period = ? AND team_key = ? AND won = 1",
                (user_id, period, team.key),
            ).fetchone()[0]
        connection.execute(
            "INSERT INTO pve_log (discord_user_id, kind, period, team_key, won) VALUES (?, ?, ?, ?, ?)",
            (user_id, kind, period, team.key, int(won)),
        )
        campaign = CAMPAIGNS.get(kind.split(":", 1)[1]) if kind.startswith("campanha:") else None
        if campaign is not None:
            gains += _advance_campaign(connection, user_id, campaign, won)
        if not won:
            return gains
        memilets = CAMPAIGN_STEP_PRIZE if campaign is not None else 0
        if kind == "escada":
            row = connection.execute(
                "SELECT ladder FROM pve_progress WHERE discord_user_id = ?", (user_id,)
            ).fetchone()
            step = int(row["ladder"]) if row else 0
            connection.execute(
                """
                INSERT INTO pve_progress (discord_user_id, ladder) VALUES (?, ?)
                ON CONFLICT(discord_user_id) DO UPDATE SET ladder = excluded.ladder
                """,
                (user_id, step + 1),
            )
            memilets = ladder_prize(step)
            if (step + 1) % 5 == 0:
                give(connection, user_id, "caixa")
                gains.append("📦 +1 caixa no inventário")
        elif kind == "livre" and not already:
            memilets = free_prize(team)
        elif kind == "dia":
            memilets = DAILY_BOSS_PRIZE
            give(connection, user_id, "caixa_epica")
            gains.append("🟠 +1 caixa épica no inventário")
        elif kind == "semana":
            memilets = WEEKLY_BOSS_PRIZE
            give(connection, user_id, "pacote_epico")
            gains.append("🎴 +1 pacote épico no inventário")
        if memilets:
            connection.execute(
                "UPDATE game_users SET memilets = memilets + ? WHERE discord_user_id = ?",
                (memilets, user_id),
            )
            gains.insert(0, f"{memilets:,} Memilets".replace(",", "."))
        gains += _check_achievements(connection, user_id)
    return gains


# --- Campanhas ------------------------------------------------------------------------


def _nation_key(country: str) -> str:
    return "selecao:" + country.lower()


@dataclass(frozen=True)
class Campaign:
    key: str
    name: str
    emoji: str
    description: str
    stages: tuple[str, ...]  # nome de cada partida (vazio = "Jogo N")
    reset_on_loss: bool
    prize: int
    epic_packs: int

    def teams(self) -> list[PveTeam]:
        by_key = {team.key: team for team in legend_teams()}
        if self.key == "brasileirao":
            return [team for team in legend_teams() if team.key.startswith("clube:")]
        if self.key == "copa":
            keys = [_nation_key(c) for c in ("Uruguai", "Portugal", "França", "Brasil")]
            return [by_key[key] for key in keys if key in by_key]
        nations = [team for team in legend_teams() if team.key.startswith("selecao:")]
        return nations[-5:] + [by_key["mundial"]]

    def stage(self, step: int) -> str:
        return self.stages[step] if step < len(self.stages) else f"Jogo {step + 1}"


CAMPAIGN_STEP_PRIZE = 300
CAMPAIGNS = {
    "brasileirao": Campaign(
        "brasileirao",
        "Lendas do Brasileirão",
        "🛡️",
        "Os 12 grandes clubes brasileiros lendários, do mais fraco ao mais forte. Perdeu, "
        "tenta o mesmo jogo de novo.",
        (),
        False,
        15_000,
        1,
    ),
    "copa": Campaign(
        "copa",
        "Copa das Lendas",
        "🏆",
        "Mata-mata contra seleções lendárias, das oitavas à final. **Perdeu, está fora**: a "
        "Copa recomeça das oitavas.",
        ("Oitavas de final", "Quartas de final", "Semifinal", "Final"),
        True,
        25_000,
        1,
    ),
    "mundial": Campaign(
        "mundial",
        "Rumo ao Mundial",
        "🌍",
        "As 5 seleções lendárias mais fortes e, no fim, a Seleção Mundial. Perdeu, tenta o "
        "mesmo jogo de novo.",
        (),
        False,
        40_000,
        2,
    ),
}


def campaign_step(connection, user_id: str, campaign: str) -> tuple[int, int]:
    row = connection.execute(
        "SELECT step, completions FROM pve_campaigns WHERE discord_user_id = ? AND campaign = ?",
        (user_id, campaign),
    ).fetchone()
    return (int(row["step"]), int(row["completions"])) if row else (0, 0)


def campaign_progress(
    discord_user_id: int | str, database_path: str | Path = DATABASE_PATH
) -> dict[str, tuple[int, int]]:
    """Campanha -> (próxima partida, vezes que completou)."""
    with connect(database_path) as connection:
        return {key: campaign_step(connection, str(discord_user_id), key) for key in CAMPAIGNS}


def _advance_campaign(connection, user_id: str, campaign: Campaign, won: bool) -> list[str]:
    step, completions = campaign_step(connection, user_id, campaign.key)
    total = len(campaign.teams())
    gains: list[str] = []
    if not won:
        if campaign.reset_on_loss and step:
            step = 0
            gains.append(f"🔁 Eliminado! A {campaign.name} recomeça do início.")
        else:
            return gains
    elif step + 1 >= total:
        step, completions = 0, completions + 1
        for _ in range(campaign.epic_packs):
            give(connection, user_id, "pacote_epico")
        gains.append(
            f"{campaign.emoji} **{campaign.name} completa!** +{campaign.prize:,}".replace(",", ".")
        )
        if campaign.epic_packs:
            gains.append(f"🎴 +{campaign.epic_packs} pacote(s) épico(s) no inventário")
        _pay(connection, user_id, campaign.prize)
    else:
        step += 1
    connection.execute(
        """
        INSERT INTO pve_campaigns (discord_user_id, campaign, step, completions)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(discord_user_id, campaign) DO UPDATE SET
            step = excluded.step, completions = excluded.completions
        """,
        (user_id, campaign.key, step, completions),
    )
    return gains


def _pay(connection, user_id: str, amount: int) -> None:
    connection.execute(
        "UPDATE game_users SET memilets = memilets + ? WHERE discord_user_id = ?",
        (amount, user_id),
    )


# --- Conquistas -----------------------------------------------------------------------


def _wins(connection, user_id: str, where: str, *args) -> int:
    return connection.execute(
        f"SELECT COUNT(*) FROM pve_log WHERE discord_user_id = ? AND won = 1 AND {where}",
        (user_id, *args),
    ).fetchone()[0]


def _ladder_done(connection, user_id: str) -> bool:
    row = connection.execute(
        "SELECT ladder FROM pve_progress WHERE discord_user_id = ?", (user_id,)
    ).fetchone()
    return bool(row) and int(row["ladder"]) >= len(WARMUP_TEAMS) + len(legend_teams())


def _completed(campaign: str):
    return lambda connection, user_id: campaign_step(connection, user_id, campaign)[1] > 0


# Conquista -> (emoji, nome, como ganhar, prêmio, regra).
ACHIEVEMENTS = {
    "primeira_lenda": (
        "⭐",
        "Primeira lenda",
        "Vença um time lendário",
        1_000,
        lambda c, u: _wins(c, u, "team_key NOT LIKE 'treino:%'") > 0,
    ),
    "chefao_dia": (
        "☀️",
        "Chefe do dia",
        "Vença um chefão do dia",
        2_000,
        lambda c, u: _wins(c, u, "kind = 'dia'") > 0,
    ),
    "cacador": (
        "🎯",
        "Caçador de chefões",
        "Vença 10 chefões do dia",
        8_000,
        lambda c, u: _wins(c, u, "kind = 'dia'") >= 10,
    ),
    "chefao_semana": (
        "🗓️",
        "Rei da semana",
        "Vença um chefão da semana",
        5_000,
        lambda c, u: _wins(c, u, "kind = 'semana'") > 0,
    ),
    "escada": ("🪜", "Topo da escada", "Complete a escada", 10_000, _ladder_done),
    "camp_brasileirao": (
        "🛡️",
        "Lenda nacional",
        "Complete as Lendas do Brasileirão",
        5_000,
        _completed("brasileirao"),
    ),
    "camp_copa": ("🏆", "Campeão da Copa", "Ganhe a Copa das Lendas", 8_000, _completed("copa")),
    "camp_mundial": (
        "🌍",
        "Rumo ao topo",
        "Complete o Rumo ao Mundial",
        10_000,
        _completed("mundial"),
    ),
    "mundial": (
        "👑",
        "Melhor do mundo",
        "Vença a Seleção Mundial",
        10_000,
        lambda c, u: _wins(c, u, "team_key LIKE 'mundial%'") > 0,
    ),
}


def earned_achievements(
    discord_user_id: int | str, database_path: str | Path = DATABASE_PATH
) -> set[str]:
    with connect(database_path) as connection:
        return {
            row["achievement"]
            for row in connection.execute(
                "SELECT achievement FROM pve_achievements WHERE discord_user_id = ?",
                (str(discord_user_id),),
            )
        }


def _check_achievements(connection, user_id: str) -> list[str]:
    """Libera (e paga) as conquistas que a pessoa acabou de cumprir."""
    have = {
        row["achievement"]
        for row in connection.execute(
            "SELECT achievement FROM pve_achievements WHERE discord_user_id = ?", (user_id,)
        )
    }
    gains = []
    for key, (emoji, name, _, prize, rule) in ACHIEVEMENTS.items():
        if key in have or not rule(connection, user_id):
            continue
        connection.execute(
            "INSERT INTO pve_achievements (discord_user_id, achievement) VALUES (?, ?)",
            (user_id, key),
        )
        _pay(connection, user_id, prize)
        gains.append(f"🏅 Conquista **{emoji} {name}**: +{prize:,}".replace(",", "."))
    return gains


PVE_TITLES = {
    "escada": "Escada",
    "dia": "Chefão do dia",
    "semana": "Chefão da semana",
    "livre": "Desafio livre",
}


def pve_title(discord_user_id, kind: str, database_path: str | Path = DATABASE_PATH) -> str:
    """Título do placar: o modo ou, numa campanha, o nome dela e a fase."""
    if kind.startswith("campanha:"):
        campaign = CAMPAIGNS[kind.split(":", 1)[1]]
        with connect(database_path) as connection:
            step, _ = campaign_step(connection, str(discord_user_id), campaign.key)
        return f"{campaign.name} · {campaign.stage(step)}"
    return PVE_TITLES.get(kind, "Desafio")


def seconds_until_tomorrow(now: datetime | None = None) -> int:
    local = (now or datetime.now(timezone.utc)).astimezone(BRASILIA)
    tomorrow = (local + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return int((tomorrow - local).total_seconds())
