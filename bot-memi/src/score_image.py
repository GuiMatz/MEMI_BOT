"""Cartão do placar final: emblemas, placar, pênaltis e artilheiros dos dois times.

`render_score_card` gera o PNG; `render_score_gif`, a versão animada enviada no fim das
partidas (uma para amistoso, outra dourada para campeonato).
"""

from __future__ import annotations

import random
from io import BytesIO
from typing import Any, Iterable

from PIL import Image, ImageChops, ImageDraw, ImageFilter

from src.visual import (
    MUTED,
    NIGHT,
    apply_grain,
    emblem,
    fit,
    font,
    glow,
    horizontal_gradient,
    mix,
    pill,
    rgb,
    shade,
    shadowed,
    text_center,
    to_png,
    vignette,
)

WIDTH, HEIGHT = 1200, 630
GOLD = (240, 200, 70)
MAX_SCORERS = 4


def _goals_by_side(goals: Iterable[Any], side: str) -> list[str]:
    lines = []
    for goal in goals:
        data = goal if isinstance(goal, dict) else goal.__dict__
        if data.get("side") != side:
            continue
        minute = f"{data['minute']}'" + (f"+{data['stoppage']}" if data.get("stoppage") else "")
        own_goal = " (contra)" if data.get("play") == "contra" else ""
        lines.append((minute, str(data.get("scorer_name") or "—") + own_goal))
    return lines


def _ball(draw: ImageDraw.ImageDraw, x: float, y: float, radius: float) -> None:
    draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill="white")
    draw.regular_polygon((x, y, radius * 0.42), 5, fill=(30, 30, 35))


def render_score_card(
    home: dict[str, Any],
    away: dict[str, Any],
    home_goals: int,
    away_goals: int,
    goals: Iterable[Any] = (),
    *,
    title: str = "Amistoso",
    venue: str | None = None,
    penalties: tuple[int, int] | None = None,
) -> BytesIO:
    home_color = rgb(home.get("color") or "#2E8B57")
    away_color = rgb(away.get("color") or "#3B6FD8")
    final = "final" in title.casefold()
    accent = GOLD if final else (235, 240, 245)

    image = horizontal_gradient((WIDTH, HEIGHT), shade(home_color, 0.55), shade(away_color, 0.55))
    middle = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    ImageDraw.Draw(middle).polygon(
        [(470, 0), (730, 0), (680, HEIGHT), (520, HEIGHT)], fill=(*NIGHT, 235)
    )
    image.alpha_composite(middle.resize((WIDTH, HEIGHT)))
    image.alpha_composite(
        glow((WIDTH, HEIGHT), (230, 250), 190, mix(home_color, (255, 255, 255), 0.2), 120)
    )
    image.alpha_composite(
        glow((WIDTH, HEIGHT), (970, 250), 190, mix(away_color, (255, 255, 255), 0.2), 120)
    )
    if final:
        image.alpha_composite(glow((WIDTH, HEIGHT), (600, 240), 170, GOLD, 70))
    image = apply_grain(image, 10)
    vignette(image, 120)
    draw = ImageDraw.Draw(image)

    pill(draw, (WIDTH // 2, 60), title.upper(), 30, accent)
    if venue:
        text_center(draw, (WIDTH // 2, 114), fit(draw, venue, font(26), 440), 26, False, MUTED)

    for team, center_x in ((home, 230), (away, 970)):
        badge = shadowed(emblem(team, 210), blur=12)
        image.alpha_composite(badge, (center_x - badge.width // 2, 255 - badge.height // 2))
        name = str(team.get("team_name") or "Time")
        size = 44
        while size > 30 and draw.textlength(name, font=font(size, True)) > 430:
            size -= 1
        text_center(draw, (center_x, 400), fit(draw, name, font(size, True), 430), size)

    home_won = home_goals > away_goals or (
        penalties is not None and home_goals == away_goals and penalties[0] > penalties[1]
    )
    away_won = away_goals > home_goals or (
        penalties is not None and home_goals == away_goals and penalties[1] > penalties[0]
    )
    score_font = 150
    text_center(
        draw,
        (WIDTH // 2 - 95, 250),
        str(home_goals),
        score_font,
        fill="white" if home_won or not away_won else (200, 205, 215),
    )
    text_center(draw, (WIDTH // 2, 245), "–", 90, fill=(150, 158, 172))
    text_center(
        draw,
        (WIDTH // 2 + 95, 250),
        str(away_goals),
        score_font,
        fill="white" if away_won or not home_won else (200, 205, 215),
    )
    if penalties:
        text_center(
            draw, (WIDTH // 2, 355), f"pênaltis {penalties[0]} × {penalties[1]}", 32, True, accent
        )
    else:
        text_center(draw, (WIDTH // 2, 355), "FIM DE JOGO", 28, True, MUTED)

    goal_list = list(goals)
    for side, center_x in (("home", 230), ("away", 970)):
        rows = _goals_by_side(goal_list, side)
        shown = rows[:MAX_SCORERS]
        y = 458
        for minute, scorer in shown:
            label = fit(draw, f"{minute}  {scorer}", font(30, True), 400)
            width = draw.textlength(label, font=font(30, True))
            left = center_x - (width + 36) / 2
            _ball(draw, left + 11, y, 11)
            draw.text((left + 32, y), label, font=font(30, True), fill="white", anchor="lm")
            y += 40
        if len(rows) > MAX_SCORERS:
            text_center(draw, (center_x, y), f"+{len(rows) - MAX_SCORERS} gols", 24, False, MUTED)

    draw.rectangle((0, HEIGHT - 10, WIDTH // 2, HEIGHT), fill=home_color)
    draw.rectangle((WIDTH // 2, HEIGHT - 10, WIDTH, HEIGHT), fill=away_color)
    text_center(draw, (WIDTH // 2, HEIGHT - 34), "FUT.AE", 22, True, (190, 198, 210))
    return to_png(image)


# --- Versão animada ---------------------------------------------------------------------
#
# Mesmo layout do cartão, montado em camadas. O primeiro quadro já traz o placar
# completo (escudos, gols, artilheiros e o selo de campeão): no Discord, quem não carrega
# o GIF vê só esse quadro. A animação vem por cima: um brilho discreto passando pelo
# centro no amistoso e poeira dourada caindo no campeonato.

GIF_SCALE = 0.8  # 960 × 504: cabe folgado no limite de anexo do Discord
FRAME_MS = 45
HOLD_MS = 4000
EFFECT_FRAMES = 22  # brilho/poeira por cima do placar já pronto


def _layer() -> Image.Image:
    return Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))


def _gif_background(home_color, away_color, title, venue, championship) -> Image.Image:
    accent = GOLD if championship else (235, 240, 245)
    image = horizontal_gradient((WIDTH, HEIGHT), shade(home_color, 0.55), shade(away_color, 0.55))
    middle = _layer()
    ImageDraw.Draw(middle).polygon(
        [(470, 0), (730, 0), (680, HEIGHT), (520, HEIGHT)], fill=(*NIGHT, 235)
    )
    image.alpha_composite(middle)
    image.alpha_composite(
        glow((WIDTH, HEIGHT), (230, 250), 190, mix(home_color, (255, 255, 255), 0.2), 120)
    )
    image.alpha_composite(
        glow((WIDTH, HEIGHT), (970, 250), 190, mix(away_color, (255, 255, 255), 0.2), 120)
    )
    if championship:
        image.alpha_composite(glow((WIDTH, HEIGHT), (600, 240), 170, GOLD, 70))
    image = apply_grain(image, 10)
    vignette(image, 120)
    draw = ImageDraw.Draw(image)
    pill(draw, (WIDTH // 2, 60), title.upper(), 30, accent)
    if venue:
        text_center(draw, (WIDTH // 2, 114), fit(draw, venue, font(26), 440), 26, False, MUTED)
    draw.rectangle((0, HEIGHT - 10, WIDTH // 2, HEIGHT), fill=home_color)
    draw.rectangle((WIDTH // 2, HEIGHT - 10, WIDTH, HEIGHT), fill=away_color)
    text_center(draw, (WIDTH // 2, HEIGHT - 34), "FUT.AE", 22, True, (190, 198, 210))
    return image


def _team_layer(team: dict[str, Any], center_x: int) -> Image.Image:
    layer = _layer()
    badge = shadowed(emblem(team, 210), blur=12)
    layer.alpha_composite(badge, (center_x - badge.width // 2, 255 - badge.height // 2))
    draw = ImageDraw.Draw(layer)
    name = str(team.get("team_name") or "Time")
    size = 44
    while size > 30 and draw.textlength(name, font=font(size, True)) > 430:
        size -= 1
    text_center(draw, (center_x, 400), fit(draw, name, font(size, True), 430), size)
    return layer


def _scorer_layers(goals: list[Any]) -> list[tuple[Image.Image, int]]:
    """Uma camada por artilheiro, na ordem em que aparecem (alternando os lados)."""
    rows: list[tuple[Image.Image, int]] = []
    for side, center_x in (("home", 230), ("away", 970)):
        entries = _goals_by_side(goals, side)
        y = 458
        for index, (minute, scorer) in enumerate(entries[:MAX_SCORERS]):
            layer = _layer()
            draw = ImageDraw.Draw(layer)
            label = fit(draw, f"{minute}  {scorer}", font(30, True), 400)
            width = draw.textlength(label, font=font(30, True))
            left = center_x - (width + 36) / 2
            _ball(draw, left + 11, y, 11)
            draw.text((left + 32, y), label, font=font(30, True), fill="white", anchor="lm")
            rows.append((layer, index * 2 + (0 if side == "home" else 1)))
            y += 40
        if len(entries) > MAX_SCORERS:
            layer = _layer()
            text_center(
                ImageDraw.Draw(layer),
                (center_x, y),
                f"+{len(entries) - MAX_SCORERS} gols",
                24,
                False,
                MUTED,
            )
            rows.append((layer, MAX_SCORERS * 2 + (0 if side == "home" else 1)))
    rows.sort(key=lambda row: row[1])
    return rows


def _digit_layer(text: str, center, scale: float, fill) -> Image.Image:
    layer = _layer()
    size = max(10, round(150 * scale))
    text_center(ImageDraw.Draw(layer), center, text, size, fill=fill)
    return layer


def _sweep(progress: float) -> Image.Image:
    """Faixa de luz diagonal que atravessa a área do placar."""
    layer = _layer()
    x = -200 + (WIDTH + 400) * progress
    band = Image.new("L", (WIDTH, HEIGHT), 0)
    ImageDraw.Draw(band).polygon([(x, 0), (x + 90, 0), (x - 60, HEIGHT), (x - 150, HEIGHT)], 60)
    center = Image.new("L", (WIDTH, HEIGHT), 0)
    ImageDraw.Draw(center).rectangle((430, 150, 770, 380), fill=255)
    layer.putalpha(ImageChops.multiply(band, center).filter(ImageFilter.GaussianBlur(18)))
    return layer


def _dust(frame: int, seed: str) -> Image.Image:
    """Poeira dourada caindo devagar (sempre igual para a mesma partida)."""
    layer = _layer()
    draw = ImageDraw.Draw(layer)
    generator = random.Random(seed)
    for _ in range(28):
        x = generator.uniform(0, WIDTH)
        speed = generator.uniform(3, 7)
        y = (generator.uniform(-HEIGHT, HEIGHT) + frame * speed) % (HEIGHT + 40) - 20
        radius = generator.uniform(1.5, 3.5)
        alpha = int(generator.uniform(90, 200))
        draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=(*GOLD, alpha))
    return layer


def render_score_gif(
    home: dict[str, Any],
    away: dict[str, Any],
    home_goals: int,
    away_goals: int,
    goals: Iterable[Any] = (),
    *,
    title: str = "Amistoso",
    venue: str | None = None,
    penalties: tuple[int, int] | None = None,
) -> BytesIO:
    """O placar final animado. Campeonato (título com "final" ou "campeonato") é dourado."""
    folded = title.casefold()
    championship = "final" in folded or "campeonato" in folded
    accent = GOLD if championship else (235, 240, 245)
    home_color = rgb(home.get("color") or "#2E8B57")
    away_color = rgb(away.get("color") or "#3B6FD8")
    background = _gif_background(home_color, away_color, title, venue, championship)
    teams = [(_team_layer(home, 230), -1), (_team_layer(away, 970), 1)]
    scorers = _scorer_layers(list(goals))

    home_won = home_goals > away_goals or (
        penalties is not None and home_goals == away_goals and penalties[0] > penalties[1]
    )
    away_won = away_goals > home_goals or (
        penalties is not None and home_goals == away_goals and penalties[1] > penalties[0]
    )
    dim = (200, 205, 215)
    digits = (
        (str(home_goals), (WIDTH // 2 - 95, 250), "white" if home_won or not away_won else dim),
        (str(away_goals), (WIDTH // 2 + 95, 250), "white" if away_won or not home_won else dim),
    )
    status = _layer()
    if penalties:
        text_center(
            ImageDraw.Draw(status),
            (WIDTH // 2, 355),
            f"pênaltis {penalties[0]} × {penalties[1]}",
            32,
            True,
            accent,
        )
    else:
        text_center(ImageDraw.Draw(status), (WIDTH // 2, 355), "FIM DE JOGO", 28, True, MUTED)
    dash = _layer()
    text_center(ImageDraw.Draw(dash), (WIDTH // 2, 245), "–", 90, fill=(150, 158, 172))
    champion = None
    if championship and (home_won or away_won):
        champion = _layer()
        pill(ImageDraw.Draw(champion), (230 if home_won else 970, 140), "CAMPEÃO", 24, GOLD)

    # O placar inteiro já no primeiro quadro (quem não carrega o GIF vê ele); a animação
    # só passa por cima: brilho no amistoso, poeira dourada no campeonato.
    content = _layer()
    for layer, _ in teams:
        content.alpha_composite(layer)
    content.alpha_composite(dash)
    for text, center, fill in digits:
        content.alpha_composite(_digit_layer(text, center, 1.0, fill))
    content.alpha_composite(status)
    for layer, _ in scorers:
        content.alpha_composite(layer)
    if champion is not None:
        content.alpha_composite(champion)
    total = EFFECT_FRAMES
    dust_seed = f"{home.get('team_name')}{away.get('team_name')}"
    frames: list[Image.Image] = []
    for frame in range(total):
        image = background.copy()
        if championship:
            image.alpha_composite(_dust(frame, dust_seed))
        image.alpha_composite(content)
        if not championship:
            image.alpha_composite(_sweep(frame / (total - 1)))
        frames.append(
            image.resize(
                (round(WIDTH * GIF_SCALE), round(HEIGHT * GIF_SCALE)), Image.LANCZOS
            ).convert("RGB")
        )

    palette = frames[-1].quantize(colors=255, method=Image.Quantize.MEDIANCUT)
    indexed = [frame.quantize(palette=palette, dither=Image.Dither.NONE) for frame in frames]
    buffer = BytesIO()
    indexed[0].save(
        buffer,
        format="GIF",
        save_all=True,
        append_images=indexed[1:],
        duration=[FRAME_MS] * (len(indexed) - 1) + [HOLD_MS],
        loop=0,
        optimize=False,
        disposal=1,
    )
    buffer.seek(0)
    return buffer
