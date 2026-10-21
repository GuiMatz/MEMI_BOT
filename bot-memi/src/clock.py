"""Fuso horário do bot: tudo que aparece para as pessoas usa Brasília (UTC-3)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

BRASILIA = timezone(timedelta(hours=-3), "BRT")


def brasilia_time(moment: datetime) -> str:
    """`14:00 (Brasília)` a partir de um horário com fuso."""
    return f"{moment.astimezone(BRASILIA):%H:%M} (Brasília)"
