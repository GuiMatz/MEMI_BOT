"""Escalação vertical do `mm!time`: os 11 titulares como mini-cartas grandes no campo.

Mostra só o que importa: nome do time, formação, OVR médio e cada titular com foto,
overall na cor da série, posição e nome. Emblema embaixo à esquerda e técnico embaixo à
direita, como no restante do jogo. Vagas vazias aparecem tracejadas com a posição pedida.
"""

from __future__ import annotations

from io import BytesIO
from typing import Any

from PIL import Image, ImageDraw, ImageOps

from src.card_image import _silhouette
from src.lineup_image import DEPTH, SIDE, TIER_COLORS, coach_placeholder
from src.ratings import rarity_index
from src.teams import FORMATIONS, STYLE_LABELS
from src.visual import MUTED, emblem, fit, font, load_picture, mix, rgb, text_center, to_png

WIDTH, HEIGHT = 1000, 1500
HEADER = 176
PITCH = (24, HEADER + 12, WIDTH - 24, HEIGHT - 24)
TILE_W, TILE_H = 160, 200
PHOTO_H = 148
GRASS = ((26, 70, 46), (29, 77, 50))
LINES = (255, 255, 255, 60)

OFF_POSITION = (232, 176, 75)  # jogador fora da posição natural


def squad_coordinates(formation: str) -> dict[int, tuple[int, int]]:
    """Centro de cada vaga (1-11): goleiro embaixo, ataque em cima."""
    positions = FORMATIONS[formation]
    lines = [int(part) for part in formation.split("-")]
    outfield = sorted(range(2, 12), key=lambda slot: (DEPTH[positions[slot - 1]], slot))
    left, top, right, bottom = PITCH
    goalkeeper_y = bottom - 150
    first, last = goalkeeper_y - 250, top + 150
    coordinates = {1: (WIDTH // 2, goalkeeper_y)}
    cursor = 0
    for index, count in enumerate(lines):
        members = sorted(
            outfield[cursor : cursor + count],
            key=lambda slot: (SIDE.get(positions[slot - 1], 1), slot),
        )
        cursor += count
        y = first if len(lines) == 1 else int(first - (first - last) * index / (len(lines) - 1))
        spacing = (right - left) / (count + 1)
        for position, slot in enumerate(members, start=1):
            coordinates[slot] = (int(left + spacing * position), y)
    return coordinates


def _pitch(image: Image.Image) -> None:
    draw = ImageDraw.Draw(image)
    left, top, right, bottom = PITCH
    stripes = 10
    height = (bottom - top) / stripes
    for index in range(stripes):
        draw.rectangle(
            (left, int(top + index * height), right, int(top + (index + 1) * height)),
            fill=GRASS[index % 2],
        )
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    lines = ImageDraw.Draw(overlay)
    middle = (top + bottom) // 2
    lines.rectangle(PITCH, outline=LINES, width=3)
    lines.line((left, middle, right, middle), fill=LINES, width=3)
    lines.ellipse(
        (WIDTH / 2 - 100, middle - 100, WIDTH / 2 + 100, middle + 100), outline=LINES, width=3
    )
    for y0, y1 in ((top, top + 170), (bottom - 170, bottom)):
        lines.rectangle((WIDTH / 2 - 220, y0, WIDTH / 2 + 220, y1), outline=LINES, width=3)
    image.alpha_composite(overlay)


def _tile(player: dict[str, Any], position: str) -> Image.Image:
    """Mini-carta: foto, rendimento na vaga na cor da série e posição.

    Fora da posição natural, o número é o que ele rende na vaga e a posição aparece
    como "PE›PD" (natural › vaga), em âmbar.
    """
    overall = int(player.get("effective_overall") or player.get("overall") or 0)
    rating = int(player.get("slot_overall") or overall)
    natural = player.get("natural_position") or position
    color = TIER_COLORS[rarity_index(overall) or 0] if overall else (120, 120, 120)
    tile = Image.new("RGBA", (TILE_W, TILE_H), (0, 0, 0, 0))
    body = Image.new("RGBA", (TILE_W, TILE_H), (14, 18, 25, 255))
    picture = load_picture(player.get("photo_path"), "players", player.get("id"))
    if picture is not None:
        photo = ImageOps.fit(picture, (TILE_W, PHOTO_H), centering=(0.5, 0.15))
    else:
        photo = _silhouette((TILE_W, PHOTO_H), color)
    body.alpha_composite(photo, (0, 0))
    draw = ImageDraw.Draw(body)
    draw.rectangle((0, PHOTO_H, TILE_W, TILE_H), fill=(14, 18, 25))
    draw.text((14, PHOTO_H + 26), str(rating or "—"), font=font(34, True), fill=color, anchor="lm")
    label, label_color = (
        (position, MUTED) if natural == position else (f"{natural}›{position}", OFF_POSITION)
    )
    draw.text(
        (TILE_W - 14, PHOTO_H + 27), label, font=font(22, True), fill=label_color, anchor="rm"
    )
    mask = Image.new("L", (TILE_W, TILE_H), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, TILE_W - 1, TILE_H - 1), 16, fill=255)
    tile.paste(body, (0, 0), mask)
    ImageDraw.Draw(tile).rounded_rectangle(
        (1, 1, TILE_W - 2, TILE_H - 2), 16, outline=(*color, 255), width=4
    )
    return tile


def _empty_tile(position: str) -> Image.Image:
    tile = Image.new("RGBA", (TILE_W, TILE_H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(tile)
    draw.rounded_rectangle((1, 1, TILE_W - 2, TILE_H - 2), 16, fill=(10, 30, 20, 150))
    for x in range(12, TILE_W - 12, 18):
        draw.line((x, 2, x + 9, 2), fill=(220, 235, 225, 160), width=3)
        draw.line((x, TILE_H - 3, x + 9, TILE_H - 3), fill=(220, 235, 225, 160), width=3)
    for y in range(12, TILE_H - 12, 18):
        draw.line((2, y, 2, y + 9), fill=(220, 235, 225, 160), width=3)
        draw.line((TILE_W - 3, y, TILE_W - 3, y + 9), fill=(220, 235, 225, 160), width=3)
    text_center(draw, (TILE_W / 2, TILE_H / 2 - 16), "+", 52, True, (220, 235, 225))
    text_center(draw, (TILE_W / 2, TILE_H / 2 + 34), position, 26, True, (220, 235, 225))
    return tile


def render_squad(team: dict[str, Any], players: list[dict[str, Any]]) -> BytesIO:
    formation = team.get("formation") or "4-3-3"
    positions = FORMATIONS[formation]
    color = rgb(team.get("color") or "#2E8B57")
    image = Image.new("RGBA", (WIDTH, HEIGHT), (12, 16, 22, 255))
    draw = ImageDraw.Draw(image)

    # Cabeçalho: nome, formação e OVR médio.
    ratings = [
        int(p.get("slot_overall") or p["effective_overall"])
        for p in players
        if p.get("effective_overall")
    ]
    average = round(sum(ratings) / len(ratings)) if ratings else 0
    name = str(team.get("team_name") or "Meu time")
    size = 56
    while size > 36 and draw.textlength(name, font=font(size, True)) > 760:
        size -= 2
    draw.text(
        (36, 66),
        fit(draw, name, font(size, True), 760),
        font=font(size, True),
        fill="white",
        anchor="lm",
    )
    style = STYLE_LABELS.get(team.get("style") or "", team.get("style") or "")
    subtitle = f"{formation}  ·  {style}  ·  {len(players)}/11 titulares".replace("  ·    ·", "  ·")
    draw.text((36, 124), subtitle, font=font(28), fill=MUTED, anchor="lm")
    if average:
        draw.text((WIDTH - 36, 62), str(average), font=font(70, True), fill="white", anchor="rm")
        draw.text((WIDTH - 36, 122), "OVR", font=font(24, True), fill=MUTED, anchor="rm")
    draw.rectangle((0, HEADER - 6, WIDTH, HEADER), fill=color)

    _pitch(image)
    draw = ImageDraw.Draw(image)
    by_slot = {int(player["slot_order"]): player for player in players}
    for slot, (x, y) in squad_coordinates(formation).items():
        position = positions[slot - 1]
        player = by_slot.get(slot)
        tile = _tile(player, position) if player else _empty_tile(position)
        image.alpha_composite(tile, (x - TILE_W // 2, y - TILE_H // 2))
        if not player:
            continue
        label = fit(
            draw, str(player.get("name") or player.get("display_name")), font(25, True), 196
        )
        width = draw.textlength(label, font=font(25, True))
        top = y + TILE_H // 2 + 8
        draw.rounded_rectangle(
            (x - width / 2 - 12, top, x + width / 2 + 12, top + 38), 10, fill=(8, 12, 18, 235)
        )
        draw.text((x, top + 19), label, font=font(25, True), fill="white", anchor="mm")

    # Emblema embaixo à esquerda e técnico embaixo à direita.
    left, _, right, bottom = PITCH
    badge = emblem(team, 120)
    image.alpha_composite(badge, (left + 28, bottom - 28 - badge.height))
    coach = team.get("coach_name")
    if coach:
        photo = load_picture(team.get("coach_photo_path"))
        if photo is not None:
            avatar = ImageOps.fit(photo, (110, 110), centering=(0.5, 0.2))
            mask = Image.new("L", (110, 110), 0)
            ImageDraw.Draw(mask).ellipse((0, 0, 109, 109), fill=255)
            round_avatar = Image.new("RGBA", (110, 110), (0, 0, 0, 0))
            round_avatar.paste(avatar, (0, 0), mask)
            avatar = round_avatar
        else:
            avatar = coach_placeholder(110, color)
        cx = right - 28 - 95
        image.alpha_composite(avatar, (cx - 55, bottom - 210))
        draw.text(
            (cx, bottom - 82),
            "TÉCNICO",
            font=font(18, True),
            fill=mix((255, 255, 255), color, 0.2),
            anchor="mm",
        )
        draw.text(
            (cx, bottom - 52),
            fit(draw, coach, font(24, True), 200),
            font=font(24, True),
            fill="white",
            anchor="mm",
        )
    return to_png(image)
