"""Emojis do FUT.AE: a moeda Memilets e o ícone redondo de cada upgrade.

Os desenhos ficam em `assets/emojis/futae/` (gerados por `scripts/gerar_icones.py`).
Quando o MeMi BOT liga, ele envia cada PNG como emoji da aplicação e troca o texto
padrão abaixo pelo emoji personalizado (ver `emojis.aplicar` na raiz). Rodando o
FUT.AE sozinho, ficam os emojis padrão.

Emojis personalizados não aparecem em títulos nem rodapés de embed: use-os só na
descrição, em campos e em mensagens.
"""

from __future__ import annotations

from src.db import ROOT
from src.perks import UPGRADES

ICON_DIR = ROOT.parent / "assets" / "emojis" / "futae"

EMOJI: dict[str, str] = {
    "memilets": "🪙",
    **{f"upg_{key}": spec.fallback_emoji for key, spec in UPGRADES.items()},
}


def memilets(amount: int | None = None) -> str:
    """`🪙 **1.250**` com o emoji da moeda (ou só o emoji, sem valor)."""
    if amount is None:
        return EMOJI["memilets"]
    return f"{EMOJI['memilets']} **{amount:,}**".replace(",", ".")


def upgrade(key: str) -> str:
    return EMOJI.get(f"upg_{key}", "🔹")
