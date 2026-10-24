"""Imagens do Pintos Corridos e da Copa IOS: chave, dia de jogos, campeão e início de edição.

Mesma linguagem do placar e da tabela: fundo azul-noite com brilho sutil, Manrope, escudos
dos times e uma cor de destaque por competição (verde no Pintos, dourado na Copa).
"""

from __future__ import annotations

import math
import random
from io import BytesIO
from typing import Any

from PIL import Image, ImageDraw

from src.visual import (
    MUTED,
    NIGHT,
    apply_grain,
    emblem,
    fit,
    font,
    glow,
    mix,
    pill,
    shadowed,
    star,
    text_center,
    to_png,
    vertical_gradient,
    vignette,
)

GOLD = (232, 184, 74)
GREEN = (88, 196, 132)
CARD = (22, 28, 39)
CARD_EDGE = (44, 53, 68)
DIM = (120, 130, 146)
LINK = (64, 76, 96)
TBD = "A definir"


def _background(size: tuple[int, int], accent, center=None) -> Image.Image:
    image = vertical_gradient(size, (20, 26, 37), (11, 14, 20)).convert("RGBA")
    width, height = size
    image.alpha_composite(
        glow(size, center or (width / 2, height * 0.35), width * 0.32, accent, 46)
    )
    image = apply_grain(image, 6)
    vignette(image, 110)
    return image


def _header(image: Image.Image, title: str, subtitle: str, accent) -> None:
    draw = ImageDraw.Draw(image)
    width = image.width
    text_center(draw, (width / 2, 58), title, 46)
    text_center(draw, (width / 2, 104), subtitle, 24, False, MUTED)
    draw.line((width / 2 - 60, 132, width / 2 + 60, 132), fill=accent, width=3)


def _name(team: dict | None) -> str:
    return (team or {}).get("team_name") or TBD


# Chave da Copa ------------------------------------------------------------------------------

BOX_W, ROW_H, BOX_PAD = 262, 36, 6
BOX_H = ROW_H * 2 + BOX_PAD * 2


def _bracket_row(image, draw, x, y, team, score, state) -> None:
    """Uma linha do confronto: escudo, nome e placar. `state`: "win", "loss", "tbd" ou "bye"."""
    color = {"win": "white", "loss": DIM, "tbd": DIM, "bye": MUTED}[state]
    if team:
        badge = emblem(team, 24)
        image.alpha_composite(badge, (int(x + 10), int(y + (ROW_H - 24) / 2)))
    else:
        draw.ellipse(
            (x + 14, y + ROW_H / 2 - 7, x + 28, y + ROW_H / 2 + 7), outline=CARD_EDGE, width=2
        )
    label = _name(team) if state != "bye" else "passa direto"
    face = font(19, state == "win")
    # O nome usa o espaço que sobra ao lado do placar (que pode ter os pênaltis).
    room = BOX_W - 44 - 14 - (draw.textlength(score, font=font(19, True)) + 10 if score else 0)
    draw.text(
        (x + 44, y + ROW_H / 2),
        fit(draw, label, face, room),
        font=face,
        fill=color,
        anchor="lm",
    )
    if score:
        draw.text(
            (x + BOX_W - 12, y + ROW_H / 2),
            score,
            font=font(19, True),
            fill="white" if state == "win" else DIM,
            anchor="rm",
        )


def _game_box(image, draw, x, y, game, teams, accent, final=False) -> None:
    """O confronto: dois times, placar (com pênaltis) e o vencedor em destaque."""
    outline = accent if final else CARD_EDGE
    draw.rounded_rectangle(
        (x, y, x + BOX_W, y + BOX_H), 12, fill=CARD, outline=outline, width=3 if final else 2
    )
    home = teams.get(game["home_user_id"]) if game else None
    away = teams.get(game["away_user_id"]) if game else None
    winner = game.get("winner_user_id") if game else None
    bye = bool(game) and game.get("walkover") == "folga"
    scores = ("", "")
    if game and game.get("played_at") and not bye:
        scores = (str(game["home_goals"]), str(game["away_goals"]))
        if game.get("home_penalties") is not None:
            scores = (
                f"{scores[0]} ({game['home_penalties']})",
                f"{scores[1]} ({game['away_penalties']})",
            )
    for index, (team, score, user) in enumerate(
        (
            (home, scores[0], game and game["home_user_id"]),
            (away, scores[1], game and game["away_user_id"]),
        )
    ):
        if bye and index == 1:
            state = "bye"
        elif winner is None:
            state = "tbd" if team is None else "win"
        else:
            state = "win" if user == winner else "loss"
        if winner is None and team is not None:
            state = "win"  # ainda não jogou: os dois em branco
        _bracket_row(image, draw, x, y + BOX_PAD + index * ROW_H, team, score, state)
    draw.line(
        (x + 10, y + BOX_PAD + ROW_H, x + BOX_W - 10, y + BOX_PAD + ROW_H),
        fill=mix(CARD_EDGE, CARD, 0.4),
        width=1,
    )


def render_bracket(
    rounds: list[dict],
    teams: dict[str, dict],
    title: str,
    subtitle: str,
    day_labels: dict[int, str] | None = None,
) -> BytesIO:
    """A chave da Copa: lado A à esquerda, lado B à direita e a final no meio.

    `rounds` vem de `league.bracket`; `teams` é o `league.team_info` (nome, cor e escudo)."""
    labels = day_labels or {}
    sides = len(rounds) - 1  # colunas de cada lado (a final fica no centro)
    first = len(rounds[0]["games"]) // 2 if sides else 1  # jogos da 1ª fase em cada lado
    gap = 26
    margin = 40
    width = margin * 2 + sides * 2 * (BOX_W + gap) + BOX_W + 40
    width = max(width, 900)
    top = 210
    spacing = BOX_H + 26
    height = top + max(first, 1) * spacing + 90
    image = _background((width, height), GOLD, (width / 2, top + (first * spacing) / 2))
    _header(image, title, subtitle, GOLD)
    draw = ImageDraw.Draw(image)
    center_x = width / 2 - BOX_W / 2

    def column_x(column: int, side: str) -> float:
        offset = margin + column * (BOX_W + gap)
        return offset if side == "A" else width - offset - BOX_W

    # Centros verticais de cada coluna (a fase seguinte fica entre os dois jogos que a formam).
    centers = [[top + spacing * index + BOX_H / 2 for index in range(first)]]
    for _ in range(1, sides):
        prev = centers[-1]
        centers.append([(prev[2 * i] + prev[2 * i + 1]) / 2 for i in range(len(prev) // 2)])

    for column in range(sides):
        stage = rounds[column]
        games = stage["games"]
        half = len(games) // 2
        for side, chunk in (("A", games[:half]), ("B", games[half:])):
            x = column_x(column, side)
            text_center(
                draw,
                (x + BOX_W / 2, top - 28),
                f"{stage_label(stage['stage'])} · {labels.get(stage['day'], '')}".strip(" ·"),
                17,
                True,
                MUTED,
            )
            for index, game in enumerate(chunk):
                y = centers[column][index] - BOX_H / 2
                _game_box(image, draw, x, y, game, teams, GOLD)
                # Linhas até a fase seguinte (ou até a final).
                if column + 1 < sides:
                    if index % 2:
                        continue
                    a, b = centers[column][index], centers[column][index + 1]
                    nxt = centers[column + 1][index // 2]
                    edge = x + BOX_W if side == "A" else x
                    joint = edge + gap / 2 if side == "A" else edge - gap / 2
                    target = column_x(column + 1, side) + (0 if side == "A" else BOX_W)
                    for yy in (a, b):
                        draw.line((edge, yy, joint, yy), fill=LINK, width=2)
                    draw.line((joint, a, joint, b), fill=LINK, width=2)
                    draw.line((joint, nxt, target, nxt), fill=LINK, width=2)
                else:
                    middle = centers[column][index]
                    final_mid = sum(centers[0]) / len(centers[0]) if centers[0] else middle
                    edge = x + BOX_W if side == "A" else x
                    target = center_x if side == "A" else center_x + BOX_W
                    joint = (edge + target) / 2
                    draw.line((edge, middle, joint, middle), fill=LINK, width=2)
                    draw.line((joint, middle, joint, final_mid), fill=LINK, width=2)
                    draw.line((joint, final_mid, target, final_mid), fill=LINK, width=2)

    final = rounds[-1]
    final_mid = sum(centers[0]) / len(centers[0]) if centers[0] else top + BOX_H / 2
    fy = final_mid - BOX_H / 2
    image.alpha_composite(glow(image.size, (width / 2, final_mid), 170, GOLD, 60))
    draw = ImageDraw.Draw(image)
    pill(
        draw, (width / 2, fy - 30), f"FINAL · {labels.get(final['day'], '')}".strip(" ·"), 18, GOLD
    )
    game = final["games"][0]
    _game_box(image, draw, center_x, fy, game, teams, GOLD, final=True)
    champion = game and game.get("winner_user_id")
    if champion and game.get("played_at"):
        team = teams.get(champion) or {}
        badge = shadowed(emblem(team, 96), blur=10)
        image.alpha_composite(badge, (int(width / 2 - badge.width / 2), int(fy + BOX_H + 10)))
        text_center(
            draw, (width / 2, fy + BOX_H + 10 + badge.height + 6), "CAMPEÃO", 20, True, GOLD
        )
    return to_png(image)


STAGE_SHORT = {"oitavas": "OITAVAS", "quartas": "QUARTAS", "semi": "SEMI", "final": "FINAL"}


def stage_label(stage: str) -> str:
    return STAGE_SHORT.get(stage, stage.upper())


# Dia de jogos -------------------------------------------------------------------------------


def render_matchday(
    games: list[dict[str, Any]], title: str, subtitle: str, accent=GREEN
) -> BytesIO:
    """Os jogos do dia num quadro só: escudos, nomes e placar no meio.

    Cada jogo: {"home", "away" (times), "home_goals", "away_goals", "penalties" (ou None),
    "tag" (fase), "walkover" (bool)}. Sem placar (None), mostra "×" e o horário fica no
    subtítulo (agenda)."""
    width = 1200
    row = 92
    height = 170 + row * max(1, len(games)) + 40
    image = _background((width, height), accent)
    _header(image, title, subtitle, accent)
    draw = ImageDraw.Draw(image)
    y = 168
    for game in games:
        draw.rounded_rectangle((40, y, width - 40, y + row - 14), 16, fill=(*CARD, 235))
        middle = y + (row - 14) / 2
        home, away = game["home"], game["away"]
        played = game.get("home_goals") is not None
        winner = None
        if played:
            if game["home_goals"] != game["away_goals"]:
                winner = "home" if game["home_goals"] > game["away_goals"] else "away"
            elif game.get("penalties"):
                winner = "home" if game["penalties"][0] > game["penalties"][1] else "away"
        for side, team, x_badge, anchor, x_text in (
            ("home", home, width / 2 - 150, "rm", width / 2 - 170),
            ("away", away, width / 2 + 106, "lm", width / 2 + 170),
        ):
            badge = emblem(team, 44)
            image.alpha_composite(badge, (int(x_badge), int(middle - 22)))
            lost = winner is not None and winner != side
            face = font(26, not lost)
            draw.text(
                (x_text, middle),
                fit(draw, _name(team), face, 330),
                font=face,
                fill=DIM if lost else "white",
                anchor=anchor,
            )
        score = f"{game['home_goals']}  ×  {game['away_goals']}" if played else "×"
        draw.rounded_rectangle(
            (width / 2 - 70, middle - 24, width / 2 + 70, middle + 24),
            12,
            fill=(12, 16, 23),
            outline=mix(accent, CARD, 0.5),
            width=2,
        )
        text_center(draw, (width / 2, middle - (6 if game.get("penalties") else 0)), score, 28)
        if game.get("penalties"):
            pens = game["penalties"]
            text_center(
                draw, (width / 2, middle + 15), f"pên. {pens[0]} × {pens[1]}", 14, True, accent
            )
        tag = "W.O." if game.get("walkover") else game.get("tag")
        if tag:
            draw.text((64, middle), tag.upper(), font=font(15, True), fill=MUTED, anchor="lm")
        y += row
    return to_png(image)


# Campeão ------------------------------------------------------------------------------------


def render_champion(
    team: dict[str, Any],
    competition: str,
    edition: int,
    final_line: str,
    vice: dict[str, Any] | None,
    prize: str,
    accent=GOLD,
) -> BytesIO:
    """Pôster do campeão: escudo grande com brilho, nome, a final, o vice e o prêmio."""
    width, height = 1200, 1100
    image = _background((width, height), accent, (width / 2, 470))
    image.alpha_composite(glow(image.size, (width / 2, 470), 330, accent, 70))
    draw = ImageDraw.Draw(image)
    # Poeira dourada fixa (a mesma para o mesmo time).
    generator = random.Random(_name(team))
    for _ in range(60):
        x, y = generator.uniform(0, width), generator.uniform(0, height)
        if abs(x - width / 2) < 340 and y > 640:
            continue  # fora do texto (nome, final, vice e prêmio)
        radius = generator.uniform(1.5, 3.5)
        alpha = int(generator.uniform(60, 170))
        draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=(*accent, alpha))
    pill(draw, (width / 2, 92), "CAMPEÃO", 30, accent)
    text_center(draw, (width / 2, 168), competition.upper(), 54)
    text_center(draw, (width / 2, 222), f"Edição {edition}", 26, False, MUTED)
    for offset in (-1, 1):
        draw.polygon(star(width / 2 + offset * 150, 222, 14, 6), fill=accent)
    badge = shadowed(emblem(team, 380), blur=22, opacity=170)
    image.alpha_composite(badge, (int(width / 2 - badge.width / 2), int(470 - badge.height / 2)))
    draw = ImageDraw.Draw(image)
    face = font(64, True)
    text_center(draw, (width / 2, 720), fit(draw, _name(team), face, 1000), 64)
    text_center(draw, (width / 2, 784), final_line, 28, True, (215, 222, 232))
    draw.line((width / 2 - 260, 836, width / 2 + 260, 836), fill=mix(accent, NIGHT, 0.5), width=2)
    if vice:
        mini = emblem(vice, 54)
        label = f"Vice: {_name(vice)}"
        text_width = draw.textlength(label, font=font(26, True))
        start = width / 2 - (54 + 14 + text_width) / 2
        image.alpha_composite(mini, (int(start), 866))
        draw.text((start + 68, 893), label, font=font(26, True), fill=MUTED, anchor="lm")
    text_center(draw, (width / 2, 975), "PRÊMIO", 18, True, accent)
    text_center(draw, (width / 2, 1012), fit(draw, prize, font(28, True), 1060), 28)
    draw.rectangle((0, height - 10, width, height), fill=accent)
    return to_png(image)


# Início de edição ---------------------------------------------------------------------------


def render_edition_start(
    teams: list[dict[str, Any]], edition: int, calendar: str, waiting: int = 0
) -> BytesIO:
    """Os times da edição em grade, com o calendário embaixo."""
    columns = 4
    rows = max(1, math.ceil(len(teams) / columns))
    cell_w, cell_h = 260, 190
    width = 40 * 2 + columns * cell_w + (columns - 1) * 20
    height = 190 + rows * (cell_h + 20) + 130
    image = _background((width, height), GREEN)
    _header(image, f"EDIÇÃO {edition}", "PINTOS CORRIDOS  +  COPA IOS", GREEN)
    draw = ImageDraw.Draw(image)
    for index, team in enumerate(teams):
        row, column = divmod(index, columns)
        # A última linha incompleta fica centralizada.
        in_row = min(columns, len(teams) - row * columns)
        left = 40 + (columns - in_row) * (cell_w + 20) / 2
        x = left + column * (cell_w + 20)
        y = 180 + row * (cell_h + 20)
        draw.rounded_rectangle((x, y, x + cell_w, y + cell_h), 18, fill=(*CARD, 235))
        badge = emblem(team, 92)
        image.alpha_composite(badge, (int(x + cell_w / 2 - 46), int(y + 22)))
        face = font(22, True)
        text_center(draw, (x + cell_w / 2, y + 146), fit(draw, _name(team), face, cell_w - 30), 22)
    footer = 180 + rows * (cell_h + 20) + 30
    text_center(draw, (width / 2, footer + 10), calendar, 24, True, (215, 222, 232))
    note = f"{len(teams)} times · jogos todo dia às 19h"
    if waiting:
        note += f" · {waiting} na lista de espera"
    text_center(draw, (width / 2, footer + 52), note, 20, False, MUTED)
    return to_png(image)
