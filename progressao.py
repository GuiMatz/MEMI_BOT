"""Progressão do MeMi BOT: XP, níveis de 1 a 100 e patentes.

Sem dependências: o banco guarda só os totais (mensagens, músicas pedidas e roletadas) e o nível
é sempre calculado a partir deles. Para mudar o ritmo, edite as constantes abaixo; para renomear
uma patente, troque só o "nome" (o "id" identifica a tag já concedida e não deve mudar).
"""

from math import isqrt

XP_POR_MENSAGEM = 1
XP_POR_PEDIDO = 25  # uma música pedida
ROLETADAS_POR_XP = 2  # meia de XP por roletada
FATOR_NIVEL = 20  # XP para o nível N = FATOR_NIVEL * (N - 1)²
NIVEL_MAXIMO = 100

# Uma patente a cada 10 níveis; o nível máximo tem a sua própria.
PATENTES = [
    {"minimo": 1, "id": "figurante", "nome": "Figurante", "cor": 0x9AA0A6},
    {"minimo": 10, "id": "ouvinte_call", "nome": "Ouvinte da Call", "cor": 0xA1887F},
    {"minimo": 20, "id": "resenheiro", "nome": "Resenheiro", "cor": 0xCD7F32},
    {"minimo": 30, "id": "veterano_call", "nome": "Veterano da Call", "cor": 0xB0BEC5},
    {"minimo": 40, "id": "brabo_resenha", "nome": "Brabo da Resenha", "cor": 0x4FC3F7},
    {"minimo": 50, "id": "patrao_clubex", "nome": "Patrão do Clubex", "cor": 0xFFC107},
    {"minimo": 60, "id": "lenda_viva", "nome": "Lenda Viva", "cor": 0x26A69A},
    {"minimo": 70, "id": "entidade", "nome": "Entidade", "cor": 0x7E57C2},
    {"minimo": 80, "id": "mito_clubex", "nome": "Mito do Clubex", "cor": 0xEC407A},
    {"minimo": 90, "id": "divindade", "nome": "Divindade", "cor": 0xF0704E},
    {"minimo": 100, "id": "demiurgo_supremo", "nome": "Demiurgo Supremo", "cor": 0xFFD54F},
]


def xp(mensagens, pedidos, roletadas):
    """XP a partir dos totais históricos."""
    return (
        max(0, int(mensagens)) * XP_POR_MENSAGEM
        + max(0, int(pedidos)) * XP_POR_PEDIDO
        + max(0, int(roletadas)) // ROLETADAS_POR_XP
    )


def xp_minimo(n):
    """XP necessário para estar no nível `n` (1 a NIVEL_MAXIMO)."""
    n = max(1, min(NIVEL_MAXIMO, int(n)))
    return FATOR_NIVEL * (n - 1) ** 2


def nivel(total_xp):
    """Nível (1 a NIVEL_MAXIMO) de quem tem `total_xp`."""
    total_xp = max(0, int(total_xp))
    return min(NIVEL_MAXIMO, isqrt(total_xp // FATOR_NIVEL) + 1)


def progresso(total_xp):
    """(nível, avanço, meta) dentro do nível atual; no nível máximo, (NIVEL_MAXIMO, 1, 1)."""
    atual = nivel(total_xp)
    if atual >= NIVEL_MAXIMO:
        return NIVEL_MAXIMO, 1, 1
    base = xp_minimo(atual)
    return atual, max(0, int(total_xp)) - base, xp_minimo(atual + 1) - base


def patente(n):
    """Patente de quem está no nível `n`."""
    atual = PATENTES[0]
    for item in PATENTES:
        if n >= item["minimo"]:
            atual = item
    return atual


def trocou_patente(n):
    """True se o nível `n` é o primeiro de uma patente nova (o nível 1 não conta)."""
    return n > 1 and any(item["minimo"] == n for item in PATENTES)


def patentes_ate(n):
    """Patentes já alcançadas por quem está no nível `n`, da primeira à atual."""
    return [item for item in PATENTES if item["minimo"] <= max(1, n)]
