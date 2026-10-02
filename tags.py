"""Catálogo de tags do MeMi BOT: cada tag é ao mesmo tempo título e insígnia.

Para criar uma tag, acrescente uma entrada em _TAGS com um identificador novo (letras minúsculas,
números e "_", até 29 caracteres). NUNCA troque o identificador de uma tag já concedida: é ele que
fica guardado no banco. Nome, emoji e "como ganhar" podem ser editados à vontade.

A imagem da insígnia fica em assets/insignias/<imagem>. Ao ligar, o bot a envia como emoji da
aplicação e passa a usá-la no lugar do `emoji` (ver emojis.py); sem imagem, fica o emoji padrão.
"""

import re
import unicodedata
from pathlib import Path

import progressao

PASTA_INSIGNIAS = Path(__file__).with_name("assets") / "insignias"
ICONE_PADRAO = "🏷️"

CATEGORIAS = (
    "DJs e Resenhas",
    "Roleta",
    "Cartola",
    "Eventos e Comunidade",
    "Mensagens",
    "Músicas",
    "Level",
)

# Limites das tags de meta (automáticas): (quantidade, identificador).
METAS_MENSAGENS = [
    (1000, "resenha_torta"),
    (10000, "resenha_reta"),
    (50000, "resenhudo"),
    (75000, "cafetao_resenhas"),
    (100000, "rei_resenha"),
    (200000, "demiurgo"),
]
METAS_MUSICAS = [
    (50, "dj_piolho"),
    (100, "dj_overload"),
    (150, "dj_zettabytes"),
    (200, "dj_pancaked"),
    (300, "dj_cupcake"),
    (500, "dj_roger"),
]
ROLETADAS_ROLETADOR = 1000


def _tag(nome, categoria, como, emoji, *, manual=False, imagem=None, antigos=()):
    return {
        "nome": nome,
        "categoria": categoria,
        "como": como,
        "emoji": emoji,
        "manual": manual,
        "imagem": imagem,
        "antigos": tuple(antigos),
        # Compatibilidade: desde a v2.2 toda tag é título e insígnia.
        "titulo": True,
        "insignia": True,
    }


_TAGS = {
    # DJs e Resenhas (rankings)
    "dj_call": _tag("DJ da Call", "DJs e Resenhas", "Top 1 Músicas", "🥇", imagem="dj_call.png"),
    "dj_mes": _tag(
        "DJ do Mês", "DJs e Resenhas", "Top 1 Músicas do mês", "🟣", imagem="dj_mes.png"
    ),
    "dj_ano": _tag(
        "DJ do Ano", "DJs e Resenhas", "Top 1 Músicas do ano", "🔴", imagem="dj_ano.png"
    ),
    "tagarela_chat": _tag(
        "Resenhex do Clubex",
        "DJs e Resenhas",
        "Top 1 Mensagens",
        "💬",
        imagem="tagarela_chat.png",
        antigos=("Tagarela do Chat",),
    ),
    "tagarela_mes": _tag(
        "Resenhex do Mêsex",
        "DJs e Resenhas",
        "Top 1 Mensagens do mês",
        "🟪",
        imagem="tagarela_mes.png",
        antigos=("Tagarela do Mês",),
    ),
    "tagarela_ano": _tag(
        "Resenhex do Anex",
        "DJs e Resenhas",
        "Top 1 Mensagens do ano",
        "🟥",
        imagem="tagarela_ano.png",
        antigos=("Tagarela do Ano",),
    ),
    # Roleta
    "roletador": _tag(
        "Roletador", "Roleta", "1k+ roletadas no Mudae", "🎎", imagem="roletador.png"
    ),
    # Cartola (concedidas pelo dono com mm!give)
    "cartola": _tag(
        "Cartoleiro",
        "Cartola",
        "Participar do Cartola",
        "🟠",
        manual=True,
        imagem="cartola.png",
        antigos=("CARTOLA",),
    ),
    "premier_league_ios": _tag(
        "Premier League IOS",
        "Cartola",
        "Campeão da PL IOS no Cartola",
        "🦁",
        manual=True,
        imagem="premier_league_ios.png",
    ),
    "copa_ios": _tag(
        "Copa IOS",
        "Cartola",
        "Campeão da Copa IOS no Cartola",
        "🏆",
        manual=True,
        imagem="copa_ios.png",
    ),
    "pintos_corridos": _tag(
        "Pintos Corridos",
        "Cartola",
        "Campeão do PC no Cartola",
        "🐤",
        manual=True,
        imagem="pintos_corridos.png",
    ),
    "cartoleiro_ouro": _tag(
        "Cartoleiro de Ouro",
        "Cartola",
        "Campeão da Cartola de Ouro",
        "🎩",
        manual=True,
        imagem="cartoleiro_ouro.png",
    ),
    "ios_world_cup": _tag(
        "IOS World Cup",
        "Cartola",
        "Campeão da IWC do Cartola",
        "🌍",
        manual=True,
        imagem="ios_world_cup.png",
    ),
    "ios_club_wc": _tag(
        "IOS Club WC",
        "Cartola",
        "Campeão da ICWC do Cartola",
        "🌐",
        manual=True,
        imagem="ios_club_wc.png",
    ),
    # Eventos e Comunidade
    "wplace": _tag(
        "Pintador",
        "Eventos e Comunidade",
        "Pintar algo no WPLACE",
        "🎨",
        manual=True,
        imagem="wplace.png",
        antigos=("WPLACE",),
    ),
    "bongas": _tag(
        "Bongador",
        "Eventos e Comunidade",
        "Participar do BONGAS",
        "🫏",
        manual=True,
        imagem="bongas.png",
        antigos=("BONGAS",),
    ),
    "breca": _tag(
        "BrécaGames",
        "Eventos e Comunidade",
        "Ser Bréca",
        "🧱",
        manual=True,
        imagem="breca.png",
        antigos=("Bréca Games",),
    ),
    "papagaio": _tag(
        "Papagaio da Call",
        "Eventos e Comunidade",
        "Ser Papagaio",
        "🦜",
        manual=True,
        imagem="papagaio.png",
    ),
    # Metas de mensagens
    "resenha_torta": _tag(
        "Resenha Torta", "Mensagens", "1k+ mensagens", "🌀", imagem="resenha_torta.png"
    ),
    "resenha_reta": _tag(
        "Resenha Reta", "Mensagens", "10k+ mensagens", "📏", imagem="resenha_reta.png"
    ),
    "resenhudo": _tag("Resenhudo", "Mensagens", "50k+ mensagens", "🗣️", imagem="resenhudo.png"),
    "cafetao_resenhas": _tag(
        "Cafetão das Resenhas",
        "Mensagens",
        "75k+ mensagens",
        "🕶️",
        imagem="cafetao_resenhas.png",
    ),
    "rei_resenha": _tag(
        "Rei da Resenha", "Mensagens", "100k+ mensagens", "👑", imagem="rei_resenha.png"
    ),
    "demiurgo": _tag(
        "Demiurgo do Clubex", "Mensagens", "200k+ mensagens", "🌌", imagem="demiurgo.png"
    ),
    # Metas de músicas
    "dj_piolho": _tag("DJ Piolho", "Músicas", "50+ músicas pedidas", "🎶", imagem="dj_piolho.png"),
    "dj_overload": _tag(
        "DJ Overload", "Músicas", "100+ músicas pedidas", "🔊", imagem="dj_overload.png"
    ),
    "dj_zettabytes": _tag(
        "DJ Zettabytes", "Músicas", "150+ músicas pedidas", "💾", imagem="dj_zettabytes.png"
    ),
    "dj_pancaked": _tag(
        "DJ Pancaked King", "Músicas", "200+ músicas pedidas", "🥞", imagem="dj_pancaked.png"
    ),
    "dj_cupcake": _tag(
        "DJ Cupcake Party", "Músicas", "300+ músicas pedidas", "🧁", imagem="dj_cupcake.png"
    ),
    "dj_roger": _tag(
        "DJ Roger Lake", "Músicas", "500+ músicas pedidas", "🌊", imagem="dj_roger.png"
    ),
}
# Patentes: uma tag por patente, concedida ao alcançar o nível (ver progressao.py).
for _p in progressao.PATENTES:
    _TAGS["patente_" + _p["id"]] = _tag(
        _p["nome"], "Level", f"Chegar ao nível {_p['minimo']}", "🎖️"
    )

CATALOGO = _TAGS

# Emoji personalizado de cada tag ({id: "<:mm_id:123>"}), preenchido ao ligar (emojis.py).
ICONES = {}


def _chave(texto):
    texto = unicodedata.normalize("NFKD", str(texto or "").casefold())
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", texto.replace("_", " ")).strip()


def buscar(texto):
    """Identificador da tag pelo nome, identificador ou nome antigo (sem acento), ou None."""
    alvo = _chave(texto)
    if not alvo:
        return None
    for ident, info in CATALOGO.items():
        if alvo in {_chave(ident), _chave(info["nome"]), *map(_chave, info["antigos"])}:
            return ident
    return None


def manuais():
    return [ident for ident, info in CATALOGO.items() if info["manual"]]


def icone(ident):
    """Emoji da tag: o personalizado, se já enviado, ou o padrão do catálogo."""
    if ident in ICONES:
        return ICONES[ident]
    return CATALOGO.get(ident, {}).get("emoji") or ICONE_PADRAO


def rotulo(ident):
    """'<emoji> Nome' da tag."""
    return f"{icone(ident)} {CATALOGO.get(ident, {}).get('nome', ident)}"


def id_patente(item):
    return "patente_" + item["id"]
