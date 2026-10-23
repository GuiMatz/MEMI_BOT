"""Cor da linha lateral das mensagens: ela conta o que está acontecendo no conteúdo.

- Carta de jogador (roll, elenco, scout): a cor da série de raridade, a mesma da
  moldura da carta e da imagem do time.
- Partida: a cor do time que acabou de agir (gol, chance, expulsão); no apito final,
  a do vencedor.
- Estados: sucesso, perigo, alerta, dinheiro, desejo e neutro.
"""

from __future__ import annotations

import discord

from src.lineup_image import TIER_COLORS
from src.ratings import rarity_index

SUCCESS = discord.Color(0x3BA55D)
DANGER = discord.Color(0xED4245)
WARNING = discord.Color(0xF0A030)
MONEY = discord.Color(0xF1C40F)
WISH = discord.Color(0xEB459E)
NEUTRAL = discord.Color(0x4F545C)
LIVE = discord.Color(0xE03C3C)


def rgb_color(rgb: tuple[int, int, int]) -> discord.Color:
    return discord.Color.from_rgb(*rgb)


def rarity_color(overall: int | None) -> discord.Color:
    """Cor da série do overall (cinza sem nota)."""
    if not overall:
        return NEUTRAL
    return rgb_color(TIER_COLORS[rarity_index(int(overall)) or 0])


def team_color(hex_color: str | None, fallback: discord.Color = NEUTRAL) -> discord.Color:
    try:
        return discord.Color.from_str(str(hex_color))
    except (ValueError, TypeError):
        return fallback
