"""Renderizador local da escalação em imagem PNG."""

from __future__ import annotations

from collections import defaultdict
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from src.db import ROOT
from src.teams import POSITION_GROUPS, FORMATIONS

WIDTH = 1000
HEIGHT = 720
FIELD = (38, 115, 73)
LINE = (231, 244, 232)


def render_lineup(
    team: dict[str, Any],
    players: list[dict[str, Any]],
) -> BytesIO:
    if team["formation"] not in FORMATIONS:
        raise ValueError("A formação do time não é válida.")
    image = Image.new("RGB", (WIDTH, HEIGHT), (22, 29, 36))
    draw = ImageDraw.Draw(image)
    title_font = ImageFont.load_default(size=30)
    text_font = ImageFont.load_default(size=17)
    small_font = ImageFont.load_default(size=14)
    draw.text((30, 20), team["team_name"], fill="white", font=title_font)
    draw.text(
        (WIDTH - 170, 32),
        team["formation"],
        fill=(210, 219, 226),
        font=text_font,
    )
    draw.rounded_rectangle((25, 75, WIDTH - 25, HEIGHT - 25), 22, fill=FIELD)
    draw.rectangle((43, 93, WIDTH - 43, HEIGHT - 43), outline=LINE, width=3)
    center_y = (HEIGHT - 25 + 75) // 2
    draw.line((43, center_y, WIDTH - 43, center_y), fill=LINE, width=3)
    draw.ellipse((WIDTH // 2 - 82, center_y - 82, WIDTH // 2 + 82, center_y + 82),
                 outline=LINE, width=3)
    draw.rectangle((340, 93, 660, 190), outline=LINE, width=3)
    draw.rectangle((340, HEIGHT - 140, 660, HEIGHT - 43), outline=LINE, width=3)

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for player in players:
        group = POSITION_GROUPS.get(player["position"])
        if group is None:
            raise ValueError(f"Posição inválida: {player['position']}")
        grouped[group].append(player)

    y_positions = {
        "A": HEIGHT - 190,
        "M": center_y - 25,
        "D": 245,
        "G": 135,
    }
    shirt_color = _rgb(team.get("color", "#2E8B57"))
    kit_image = None
    kit_path = team.get("kit_path")
    if kit_path:
        local_kit = (ROOT / kit_path).resolve()
        if local_kit.is_file():
            with Image.open(local_kit) as opened:
                kit_image = opened.convert("RGBA")
                kit_image.thumbnail((56, 62))
    for group in ("A", "M", "D", "G"):
        group_players = sorted(grouped[group], key=lambda player: player["slot_order"])
        if not group_players:
            continue
        spacing = (WIDTH - 150) / (len(group_players) + 1)
        y = y_positions[group]
        for index, player in enumerate(group_players, start=1):
            x = int(75 + spacing * index)
            radius = 39
            if kit_image is not None:
                image.paste(
                    kit_image,
                    (x - kit_image.width // 2, y - kit_image.height // 2),
                    kit_image,
                )
            else:
                draw.polygon(
                    [
                        (x - 24, y - 25),
                        (x - 45, y - 9),
                        (x - 37, y + 5),
                        (x - 29, y),
                        (x - 26, y + 30),
                        (x + 26, y + 30),
                        (x + 29, y),
                        (x + 37, y + 5),
                        (x + 45, y - 9),
                        (x + 24, y - 25),
                        (x + 12, y - 19),
                        (x - 12, y - 19),
                    ],
                    fill=shirt_color,
                    outline=(255, 255, 255),
                    width=3,
                )
                draw.arc((x - 14, y - 29, x + 14, y - 5), 0, 180, fill=LINE, width=3)
            rating = str(player["effective_overall"])
            rating_box = draw.textbbox((0, 0), rating, font=title_font)
            draw.text(
                (x - (rating_box[2] - rating_box[0]) / 2, y - 15),
                rating,
                fill="white",
                font=title_font,
            )
            player_name = str(player.get("display_name") or player["name"])[:18]
            name_box = draw.textbbox((0, 0), player_name, font=small_font)
            draw.rounded_rectangle(
                (
                    x - (name_box[2] - name_box[0]) / 2 - 8,
                    y + radius + 5,
                    x + (name_box[2] - name_box[0]) / 2 + 8,
                    y + radius + 29,
                ),
                6,
                fill=(18, 40, 30),
            )
            draw.text(
                (x - (name_box[2] - name_box[0]) / 2, y + radius + 8),
                player_name,
                fill="white",
                font=small_font,
            )
            draw.text(
                (x - 16, y + 43),
                player["position"],
                fill=LINE,
                font=small_font,
            )

    logo_path = team.get("logo_path")
    if logo_path:
        local_path = (ROOT / logo_path).resolve()
        if local_path.is_file():
            with Image.open(local_path) as logo:
                logo = logo.convert("RGBA")
                logo.thumbnail((64, 64))
                image.paste(logo, (WIDTH - 115, 15), logo)
    buffer = BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    buffer.seek(0)
    return buffer


def _rgb(value: str) -> tuple[int, int, int]:
    normalized = value.lstrip("#")
    if len(normalized) != 6:
        return (46, 139, 87)
    try:
        return tuple(int(normalized[index:index + 2], 16) for index in (0, 2, 4))
    except ValueError:
        return (46, 139, 87)
