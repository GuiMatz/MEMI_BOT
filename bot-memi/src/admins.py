"""Quem administra o bot: o dono e os admins extras.

Uma lista só para tudo: os comandos `mm!admin` do FUT.AE, os comandos de dono do bot
principal (`mm!read`, `mm!reiniciar`...) e os de campeonato (`ativar`, `jogar`, `adiantar`,
que também aceitam quem tem Gerenciar servidor). Para incluir mais gente sem mexer no
código, use a variável `MEMI_ADMIN_IDS` com os IDs separados por vírgula.
"""

from __future__ import annotations

import os

OWNER_ID = 277898482960826369
EXTRA_ADMINS = {
    302873192706605057,  # lurram
}


def owner_id() -> int:
    return int(os.getenv("MEMI_OWNER_ID", OWNER_ID))


def admin_ids() -> set[int]:
    extra = {
        int(value)
        for value in os.getenv("MEMI_ADMIN_IDS", "").replace(";", ",").split(",")
        if value.strip().isdigit()
    }
    return {owner_id(), OWNER_ID, *EXTRA_ADMINS, *extra}


def is_admin(user_id: int | str | None) -> bool:
    try:
        return int(user_id) in admin_ids()
    except (TypeError, ValueError):
        return False
