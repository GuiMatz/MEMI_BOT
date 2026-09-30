"""MeMi BOT: rankings de música, atividade, perfis, níveis e conquistas para um servidor Discord.
Leia LEIA-ME.md para instalação, migração, catálogo e testes ao vivo.
O banco original é estendido, com backup antes da primeira migração.
"""

import asyncio
import gzip
import heapq
import json
import logging
import os
import shutil
import socket
import unicodedata
from logging.handlers import RotatingFileHandler
import csv
import io
import re
import sqlite3
import time
from collections import Counter
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import aiohttp
import changelog as versoes_bot
import discord
import imagens
from discord.ext import commands
from estilo import (
    BOT_NAME,
    COR_PADRAO,
    EMOJI,
    MESES_EXTENSO,
    barra,
    campo,
    embed_ajuda,
    embed_changelog,
    embed_nivel,
    embed_ranking,
    embed_resumo,
    linha_faixa,
    milhar,
    plural,
    responder,
    rotulo_periodo,
)
from estilo import rodape as rodape_do_bot

# --- CONFIGURAÇÃO ---------------------------------------------------------
# O token fica no arquivo "token.txt" na mesma pasta do bot (só o token, nada mais).
_ARQ_TOKEN = Path(__file__).with_name("token.txt")
TOKEN = _ARQ_TOKEN.read_text(encoding="utf-8").strip() if _ARQ_TOKEN.exists() else ""

PREFIX = "mm!"

# O bot acompanha todos os chats acessíveis; o config.json só guarda os IDs do dono,
# do canal de avisos e do servidor.
_ARQ_CONFIG = Path(__file__).with_name("config.json")
CONFIG = json.loads(_ARQ_CONFIG.read_text(encoding="utf-8")) if _ARQ_CONFIG.exists() else {}
if not isinstance(CONFIG, dict):
    raise ValueError("config.json precisa conter um objeto JSON.")


def _id_config(chave: str, variavel: str) -> int:
    return int(os.getenv(variavel, str(CONFIG.get(chave, 0))))


# Trechos do nome dos bots de música (minúsculo). () aceita qualquer bot.
# Pancake -> embed "Now Playing" (formato "Título - Artista")
# Jockie  -> embed "Started playing" (formato "Título by Artista")
NOMES_BOTS_MUSICA = ("pancake", "jockie")

# Mensagem de PESSOA que conta como pedido de música.
# Pega: m!play, m!p, p!play, p!p (maiúsculas não importam). NÃO pega m!pause, m!playlist...
RE_PEDIDO = re.compile(r"^\s*[mp]!\s*(?:play|p)\b", re.I)

# Nomes que ativam cada ranking: mm!musicas <nome>
NOMES_RANKING_ARTISTA = ("artista", "artistas")
NOMES_RANKING_USUARIO = ("ios", "usuario", "usuarios", "usuário", "usuários", "pedidos", "users")
NOMES_RANKING_MUSICA = ("", "musica", "musicas", "música", "músicas", "geral")

# Fuso usado no Wrapped e nas planilhas (Brasília). O ID do Discord guarda o horário em UTC.
FUSO = timezone(timedelta(hours=-3))
MESES = ("jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez")
DIAS_SEMANA = ("segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo")

# mm!aleatoria mostra este comando (num embed) pra pessoa copiar e colar no chat
COMANDO_PLAY = "m!play"
DEEZER_API = "https://api.deezer.com"  # API pública, não precisa de chave
DEEZER_ESPERA = 8  # segundos máx. esperando o Deezer por comando
DEEZER_REPETIR_APOS = 7 * 24 * 3600  # quando não achou capa/gênero, tenta de novo depois de 7 dias
YOUTUBE_OEMBED = "https://www.youtube.com/oembed"  # títulos de links: públicos, sem chave
SPOTIFY_OEMBED = "https://open.spotify.com/oembed"
LINK_ESPERA = 6  # segundos máx. esperando o título de um link
LINK_REPETIR_APOS = 24 * 3600  # link cujo título não veio: tenta de novo depois de 1 dia
WRAPPED_TOP_GENERO = 10  # quantos pedidos mais feitos entram na conta do gênero favorito
LIMITE_BUSCA_NOMES = 150  # máx. de pessoas buscadas na API ao exportar (o resto sai só com o ID)

POR_PAGINA = 10  # itens por página do ranking
ARQUIVO_BANCO = Path(__file__).with_name("memi.db")  # fica na mesma pasta do bot
MANUTENCAO_INTERVALO = 15  # segundos entre passadas da manutenção (avisos, fechamento, backup)
IMAGEM_TIMEOUT = 15  # segundos para gerar uma imagem (em thread) antes de desistir
SYNC_PERIODICO = 900  # segundos entre leituras de segurança (eventos perdidos sem reconexão)
SYNC_REPETIR_APOS_ERRO = 900  # segundos de espera antes de repetir uma leitura que falhou
PORTA_UNICA = 47653  # impede abrir o bot duas vezes (senão ele responderia em dobro)
# --------------------------------------------------------------------------


# ===== Extração: título + artista ==========================================
def limpar(txt: str) -> str:
    txt = re.sub(r"\]\(https?://[^)]+\)", "]", txt)  # tira link markdown
    return re.sub(r"[\[\]*_`]", "", txt).strip()


def normalizar(txt: str) -> str:
    """Ignora maiúsculas e espaços extras."""
    return re.sub(r"\s+", " ", txt.casefold()).strip()


def normalizar_artista(artista: str) -> str:
    # o YouTube às vezes coloca "Artista - Topic"
    return normalizar(re.sub(r"\s*-\s*topic$", "", artista, flags=re.I))


def chave_musica(titulo: str, artista: str) -> str:
    """Identifica a música de forma única: título + artista normalizados."""
    return f"{normalizar(titulo)}|{normalizar_artista(artista)}"


RE_TRACO = re.compile(r"\s+[-–—]\s+")  # Pancake: "Título - Artista"
RE_BY = re.compile(r"\s+by\s+", re.I)  # Jockie:  "Título by Artista"
RE_JOCKIE = re.compile(r"started playing\s*(.+)", re.I | re.S)


def dividir(nome: str, separador):
    """Divide no ÚLTIMO separador (assim 'Stand by Me by Ben E. King' funciona)."""
    achados = list(separador.finditer(nome))
    if not achados:
        return nome, ""
    m = achados[-1]
    titulo, artista = nome[: m.start()].strip(), nome[m.end() :].strip()
    if not titulo or not artista:
        return nome, ""
    return titulo, artista


def extrair_musica(msg):
    """Retorna (titulo, artista) se a mensagem for um aviso de 'tocando agora', senão None."""
    if not msg.author.bot:
        return None
    nome_autor = msg.author.name.lower()
    if NOMES_BOTS_MUSICA and not any(n in nome_autor for n in NOMES_BOTS_MUSICA):
        return None

    for e in msg.embeds:
        titulo = e.title or ""
        desc = e.description or ""

        # Pancake: título "Now Playing", "Título - Artista" na 1ª linha da descrição
        if "now playing" in titulo.lower():
            linhas = [l for l in desc.split("\n") if l.strip()]
            if linhas:
                nome = limpar(linhas[0])
                if nome:
                    return dividir(nome, RE_TRACO)

        # Jockie: "Started playing [Título by Artista](link)"
        m = RE_JOCKIE.search(f"{titulo}\n{desc}")
        if m:
            nome = limpar(m.group(1).split("\n")[0])
            if nome:
                return dividir(nome, RE_BY)
    return None


def eh_pedido(msg) -> bool:
    """True se uma PESSOA escreveu m!play / p!play (ou m!p / p!p)."""
    return (not msg.author.bot) and bool(RE_PEDIDO.match(getattr(msg, "content", "") or ""))


def consulta_do_pedido(conteudo):
    """Tira o 'm!play' do começo e devolve só o que a pessoa pediu.
    Retorna None se o texto não foi guardado (pedido antigo) e '' se não sobrou nada."""
    if conteudo is None:
        return None
    return RE_PEDIDO.sub("", conteudo, count=1).strip()


def celula(valor):
    """Evita 'injeção de fórmula' no Excel: texto que começa com = + - @ vira texto puro."""
    if isinstance(valor, str) and valor[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + valor
    return valor


def gerar_csv(cabecalho, linhas) -> io.BytesIO:
    """CSV com ';' e UTF-8 com BOM: o Excel em português abre direto, com acentos e colunas certas."""
    buf = io.StringIO()
    escritor = csv.writer(buf, delimiter=";")
    escritor.writerow(cabecalho)
    for linha in linhas:
        escritor.writerow([celula(c) for c in linha])
    return io.BytesIO(buf.getvalue().encode("utf-8-sig"))


def configurar_log(caminho=None, nivel=logging.INFO):
    """Log rotativo (3 arquivos de até 2 MB) na raiz. Devolve o handler para quem quiser fechá-lo."""
    caminho = Path(caminho) if caminho else Path(__file__).with_name("memi_bot.log")
    handler = RotatingFileHandler(caminho, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    raiz = logging.getLogger()
    raiz.addHandler(handler)
    raiz.setLevel(nivel)
    return handler


class FluxoLog:
    """Substitui stdout/stderr quando não há console (pythonw): cada linha vira um registro de log."""

    encoding = "utf-8"

    def __init__(self, nome="memi.stdout", nivel=logging.ERROR):
        self.logger, self.nivel, self._pendente = logging.getLogger(nome), nivel, ""

    def write(self, texto):
        self._pendente += texto
        *linhas, self._pendente = self._pendente.split("\n")
        for linha in linhas:
            if linha.strip():
                self.logger.log(self.nivel, linha.rstrip())
        return len(texto)

    def flush(self):
        pass

    def isatty(self):
        return False


def travar_instancia(porta=PORTA_UNICA):
    """Socket que marca esta cópia como a única em execução, ou None se já existe outra.
    Mantenha a referência viva enquanto o bot rodar."""
    trava = socket.socket()
    try:
        trava.bind(("127.0.0.1", porta))
    except OSError:
        trava.close()
        return None
    return trava


def intent_membros(valor):
    """Server Members Intent vem ligada; só a variável MEMI_MEMBERS_INTENT=0 a desliga."""
    return valor != "0"


MSG_INTENT_MEMBROS = (
    "O Discord recusou uma intent privilegiada; o bot tentou seguir sem a Server Members Intent "
    "(provavelmente desativada no Developer Portal), e quem saiu do servidor continua aparecendo "
    "nos rankings. Para esconder essas pessoas, ative-a em Developer Portal > Bot > Privileged "
    "Gateway Intents e reinicie o bot."
)
MSG_INTENT_MENSAGENS = (
    "O Discord recusou a Message Content Intent. Ative-a em Developer Portal > Bot > Privileged "
    "Gateway Intents e reinicie o bot."
)


CORES = {
    "vermelho": 0xED4245,
    "laranja": 0xE67E22,
    "amarelo": 0xF1C40F,
    "dourado": 0xFFD700,
    "verde": 0x57F287,
    "ciano": 0x1ABC9C,
    "azul": 0x3498DB,
    "roxo": 0x9B59B6,
    "rosa": 0xEB459E,
    "marrom": 0x8B5A2B,
    "cinza": 0x95A5A6,
    "branco": 0xFFFFFF,
    "preto": 0x000001,  # 0 o Discord trata como "sem cor"
}
NOMES_COR_PADRAO = ("padrao", "resetar", "reset", "remover")


def interpretar_cor(texto):
    """Cor de embed a partir de #RRGGBB / RRGGBB / 0xRRGGBB / #RGB ou de um nome de CORES.
    Devolve None para voltar à cor padrão; ValueError se não entender."""
    t = sem_acento(texto or "").strip()
    if t in NOMES_COR_PADRAO:
        return None
    if t in CORES:
        return CORES[t]
    t = t[1:] if t.startswith("#") else t[2:] if t.startswith("0x") else t
    if len(t) == 3:
        t = "".join(c * 2 for c in t)
    if len(t) != 6 or any(c not in "0123456789abcdef" for c in t):
        raise ValueError(f"cor inválida: {texto!r}")
    return int(t, 16) or CORES["preto"]


def data_local(message_id: int) -> datetime:
    """Horário (fuso de Brasília) em que a mensagem foi enviada, tirado do próprio ID."""
    return discord.utils.snowflake_time(message_id).astimezone(FUSO)


# ===== Configuração da versão 2 =============================================
MEMI_ID = _id_config("owner_id", "MEMI_OWNER_ID")
CANAL_AVISOS = _id_config("notice_channel_id", "MEMI_NOTICE_CHANNEL_ID")
# Precisa estar ativa no Developer Portal (Server Members Intent); é ela que permite tirar
# dos rankings quem saiu do servidor. MEMI_MEMBERS_INTENT=0 desliga (e quem saiu volta a aparecer).
MEMBERS_INTENT = intent_membros(os.getenv("MEMI_MEMBERS_INTENT"))
# Opcional se o bot só está em um servidor ou enxerga CANAL_AVISOS.
SERVIDOR_ID = _id_config("guild_id", "MEMI_GUILD_ID")
PREFIXO_MUDAE = "$"
COMANDOS_MUDAE = {"w", "wa", "wg", "wx", "h", "ha", "hg", "hx", "m", "ma", "mg", "mx"}
# Acrescente waifu/husbando/marry somente depois de confirmar no servidor.
PONTOS_NIVEL = [
    (0, 1),
    (1000, 50),
    (5000, 100),
    (10000, 150),
    (25000, 200),
    (50000, 250),
    (75000, 275),
    (100000, 400),
    (125000, 500),
    (150000, 650),
    (175000, 800),
    (200000, 1000),
]
TITULOS_MENSAGENS = [
    (1000, "resenha_torta", "Resenha Torta"),
    (10000, "resenha_reta", "Resenha Reta"),
    (50000, "resenhudo", "Resenhudo"),
    (75000, "cafetao_resenhas", "Cafetão das Resenhas"),
    (100000, "rei_resenha", "Rei da Resenha"),
    (200000, "demiurgo", "Demiurgo do Clubex"),
]
TITULOS_MUSICAS = [
    (50, "dj_piolho", "DJ PIOLHO"),
    (100, "dj_overload", "DJ OVERLOAD"),
    (150, "dj_zettabytes", "DJ ZETTABYTES"),
    (200, "dj_pancaked", "DJ PANCAKED KING"),
    (300, "dj_cupcake", "DJ CUPCAKE PARTY"),
    (500, "dj_roger", "DJ ROGER LAKE"),
]

# Catálogo editável. Não troque os identificadores de itens já concedidos.
# Cada item pode ser título, insígnia ou ambos. Edite nomes/emojis aqui.
CATALOGO = {
    "dj_call": {
        "nome": "DJ da Call",
        "titulo": True,
        "insignia": True,
        "emoji": "🥇",
        "manual": False,
    },
    "dj_mes": {
        "nome": "DJ do Mês",
        "titulo": True,
        "insignia": True,
        "emoji": "🟣",
        "manual": False,
    },
    "dj_ano": {
        "nome": "DJ do Ano",
        "titulo": True,
        "insignia": True,
        "emoji": "🔴",
        "manual": False,
    },
    "tagarela_chat": {
        "nome": "Tagarela do Chat",
        "titulo": True,
        "insignia": True,
        "emoji": "💬",
        "manual": False,
    },
    "tagarela_mes": {
        "nome": "Tagarela do Mês",
        "titulo": True,
        "insignia": True,
        "emoji": "🟪",
        "manual": False,
    },
    "tagarela_ano": {
        "nome": "Tagarela do Ano",
        "titulo": True,
        "insignia": True,
        "emoji": "🟥",
        "manual": False,
    },
    "roletador": {
        "nome": "Roletador",
        "titulo": True,
        "insignia": True,
        "emoji": "🎎",
        "manual": False,
    },
    "cartola": {
        "nome": "Cartoleiro",
        "nome_insignia": "CARTOLA",
        "titulo": True,
        "insignia": True,
        "emoji": "🟠",
        "manual": True,
    },
    "wplace": {
        "nome": "Pintador",
        "nome_insignia": "WPLACE",
        "titulo": True,
        "insignia": True,
        "emoji": "🎨",
        "manual": True,
    },
    "bongas": {"nome": "BONGAS", "titulo": True, "insignia": True, "emoji": "🫏", "manual": True},
    "breca": {
        "nome": "Bréca Games",
        "titulo": True,
        "insignia": True,
        "emoji": "🧱",
        "manual": True,
    },
    # Troféus do Cartola: adicione aqui depois de definir a lista, por exemplo
    # "identificador_estavel": {"nome": "Nome definido pelo dono", "titulo": True,
    #     "insignia": True, "emoji": "🏆", "manual": True},
}
for _limite, _identificador, _nome in TITULOS_MENSAGENS + TITULOS_MUSICAS:
    CATALOGO[_identificador] = {
        "nome": _nome,
        "titulo": True,
        "insignia": False,
        "emoji": "",
        "manual": False,
    }


def sem_acento(texto):
    return "".join(
        c for c in unicodedata.normalize("NFKD", normalizar(texto)) if not unicodedata.combining(c)
    )


def nivel(total):
    total = max(0, int(total))
    for (m0, n0), (m1, n1) in zip(PONTOS_NIVEL, PONTOS_NIVEL[1:]):
        if total < m1:
            return n0 + (total - m0) * (n1 - n0) // (m1 - m0)
    return 1000


def minimo_nivel(n):
    if n <= 1:
        return 0
    for (m0, n0), (m1, n1) in zip(PONTOS_NIVEL, PONTOS_NIVEL[1:]):
        if n <= n1:
            return m0 + ((n - n0) * (m1 - m0) + (n1 - n0) - 1) // (n1 - n0)
    return 200000


def progresso_valores(total):
    """(nível, avanço, meta) dentro do nível atual; no nível máximo, (1000, 1, 1)."""
    atual = nivel(total)
    if atual == 1000:
        return atual, 1, 1
    base = minimo_nivel(atual)
    return atual, total - base, minimo_nivel(atual + 1) - base


def progresso_nivel(total):
    atual, avanco, meta = progresso_valores(total)
    return atual, "Nível máximo" if atual == 1000 else f"{avanco}/{meta}"


def eh_mudae(msg):
    if msg.author.bot:
        return False
    partes = (getattr(msg, "content", "") or "").strip().casefold().split()
    return bool(
        partes
        and partes[0].startswith(PREFIXO_MUDAE)
        and partes[0][len(PREFIXO_MUDAE) :] in COMANDOS_MUDAE
    )


def intervalo(periodo="", agora=None):
    agora = (agora or datetime.now(FUSO)).astimezone(FUSO)
    periodo = sem_acento(periodo)
    if periodo == "mes":
        inicio, rotulo = (
            agora.replace(day=1, hour=0, minute=0, second=0, microsecond=0),
            "mês atual",
        )
    elif periodo == "ano":
        inicio, rotulo = (
            agora.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0),
            "ano atual",
        )
    else:
        return 0, ""
    return discord.utils.time_snowflake(inicio), rotulo


def limites_periodo(tipo, periodo):
    if tipo == "mes":
        ano, mes = map(int, periodo.split("-"))
        inicio = datetime(ano, mes, 1, tzinfo=FUSO)
        fim = datetime(ano + (mes == 12), 1 if mes == 12 else mes + 1, 1, tzinfo=FUSO)
    else:
        ano = int(periodo)
        inicio, fim = datetime(ano, 1, 1, tzinfo=FUSO), datetime(ano + 1, 1, 1, tzinfo=FUSO)
    return discord.utils.time_snowflake(inicio), discord.utils.time_snowflake(fim)


def so_memi():
    async def permitido(ctx):
        if ctx.author.id != MEMI_ID:
            raise commands.CheckFailure("Só o Memi pode usar esse comando.")
        return True

    return commands.check(permitido)


def chave_deezer(texto):
    return normalizar((texto or "").strip()[:100])


def consulta_musical(conteudo):
    """Exclui playlists/álbuns do favorito; normaliza links sem misturar faixas."""
    texto = consulta_do_pedido(conteudo)
    if not texto:
        return None
    analise = analisar_link(texto)
    if analise:
        url, _, tipo = analise
        if tipo not in ("", "música"):
            return None
        return url, texto
    if RE_LINK.search(texto):
        return None  # Link desconhecido não é classificado como faixa individual.
    return normalizar(texto), texto


def backup_sqlite(con, destino):
    """Snapshot consistente, comprimido e publicado por rename atômico."""
    destino = Path(destino)
    destino.parent.mkdir(parents=True, exist_ok=True)
    temporario = destino.with_suffix(".sqlite.tmp")
    comprimido = destino.with_suffix(".gz.tmp")
    try:
        # sqlite3.Connection.__exit__ finaliza a transação, mas não fecha o arquivo.
        # O Windows exige o fechamento explícito antes de excluir o temporário.
        with closing(sqlite3.connect(temporario)) as copia:
            con.backup(copia)
        with temporario.open("rb") as origem, gzip.open(comprimido, "wb") as saida:
            shutil.copyfileobj(origem, saida)
        comprimido.replace(destino)
    finally:
        temporario.unlink(missing_ok=True)
        comprimido.unlink(missing_ok=True)


def backup_antes_migracao(caminho):
    caminho = Path(caminho)
    if not caminho.exists():
        return
    destino = (
        caminho.parent / "backups" / ("migracao_" + datetime.now(FUSO).strftime("%Y%m%d_%H%M%S_%f"))
    )
    destino.mkdir(parents=True, exist_ok=True)
    for codigo in Path(__file__).parent.glob("*.py"):
        if not codigo.name.startswith("test_"):
            shutil.copy2(codigo, destino / codigo.name)
    with closing(sqlite3.connect(caminho)) as origem:
        backup_sqlite(origem, destino / "memi.db.gz")


# ===== Banco de dados ======================================================
class BancoLegado:
    """
    musicas -> UMA linha por música (título + artista únicos). Nunca repete.
    tocadas -> UMA linha por vez que tocou (ID da mensagem do Discord é a chave: não duplica).
    pedidos -> UMA linha por mensagem de pessoa pedindo música (ID da mensagem: não duplica).
    """

    def __init__(self, caminho):
        self.con = sqlite3.connect(caminho)

        # Esquemas antigos incompatíveis exigem migração específica; nunca descartar.
        colunas = [r[1] for r in self.con.execute("PRAGMA table_info(tocadas)")]
        if colunas and "musica_id" not in colunas:
            raise RuntimeError("Esquema antigo incompatível; nenhum dado foi apagado.")

        self.con.executescript("""
            CREATE TABLE IF NOT EXISTS musicas (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                chave         TEXT NOT NULL UNIQUE,
                titulo        TEXT NOT NULL,
                artista       TEXT NOT NULL DEFAULT '',
                artista_chave TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS tocadas (
                message_id INTEGER PRIMARY KEY,
                channel_id INTEGER NOT NULL,
                musica_id  INTEGER NOT NULL REFERENCES musicas(id),
                bot        TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_tocadas_musica ON tocadas(musica_id);
            CREATE TABLE IF NOT EXISTS pedidos (
                message_id INTEGER PRIMARY KEY,
                channel_id INTEGER NOT NULL,
                usuario_id INTEGER NOT NULL,
                conteudo   TEXT
            );
            CREATE TABLE IF NOT EXISTS canais_lidos (
                channel_id    INTEGER PRIMARY KEY,
                ultimo_msg_id INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS deezer_cache (
                chave      TEXT PRIMARY KEY,
                capa       TEXT NOT NULL DEFAULT '',
                genero     TEXT NOT NULL DEFAULT '',
                buscado_em INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS links_cache (
                chave      TEXT PRIMARY KEY,
                nome       TEXT NOT NULL DEFAULT '',
                buscado_em INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS atividade (
                autor_id  INTEGER PRIMARY KEY,
                eh_bot    INTEGER NOT NULL,
                mensagens INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS atividade_scan (
                id    INTEGER PRIMARY KEY CHECK (id = 1),
                quando INTEGER NOT NULL
            );
            """)

        # banco criado por versão anterior: ganha a coluna do artista normalizado
        if "artista_chave" not in [r[1] for r in self.con.execute("PRAGMA table_info(musicas)")]:
            self.con.execute(
                "ALTER TABLE musicas ADD COLUMN artista_chave TEXT NOT NULL DEFAULT ''"
            )
            for mid, artista in self.con.execute("SELECT id, artista FROM musicas").fetchall():
                self.con.execute(
                    "UPDATE musicas SET artista_chave = ? WHERE id = ?",
                    (normalizar_artista(artista), mid),
                )

        # banco criado antes do perfil/wrapped: a tabela de pedidos ganha a coluna do texto
        if "conteudo" not in [r[1] for r in self.con.execute("PRAGMA table_info(pedidos)")]:
            self.con.execute("ALTER TABLE pedidos ADD COLUMN conteudo TEXT")
        self.con.commit()

    def total(self) -> int:
        return self.con.execute("SELECT COUNT(*) FROM tocadas").fetchone()[0]

    def total_pedidos(self) -> int:
        return self.con.execute("SELECT COUNT(*) FROM pedidos").fetchone()[0]

    def inserir(self, linhas):
        """linhas: (message_id, channel_id, titulo, artista, bot). Mensagem repetida é ignorada."""
        cur = self.con.cursor()
        for message_id, channel_id, titulo, artista, bot in linhas:
            chave = chave_musica(titulo, artista)
            cur.execute(
                "INSERT OR IGNORE INTO musicas (chave, titulo, artista, artista_chave) "
                "VALUES (?, ?, ?, ?)",
                (chave, titulo, artista, normalizar_artista(artista)),
            )
            musica_id = cur.execute("SELECT id FROM musicas WHERE chave = ?", (chave,)).fetchone()[
                0
            ]
            cur.execute(
                "INSERT OR IGNORE INTO tocadas (message_id, channel_id, musica_id, bot) "
                "VALUES (?, ?, ?, ?)",
                (message_id, channel_id, musica_id, bot),
            )
        self.con.commit()

    def marca(self, channel_id: int) -> int:
        row = self.con.execute(
            "SELECT ultimo_msg_id FROM canais_lidos WHERE channel_id = ?", (channel_id,)
        ).fetchone()
        return row[0] if row else 0

    def salvar_marca(self, channel_id: int, msg_id: int):
        self.con.execute(
            "INSERT INTO canais_lidos (channel_id, ultimo_msg_id) VALUES (?, ?) "
            "ON CONFLICT(channel_id) DO UPDATE SET ultimo_msg_id = excluded.ultimo_msg_id",
            (channel_id, msg_id),
        )
        self.con.commit()

    def ranking_musicas(self, desde=0, ate=None):
        """[(titulo, artista, vezes_tocada), ...] da mais tocada pra menos tocada.
        desde/ate: só conta IDs de mensagem em [desde, ate) (o ID carrega a data)."""
        filtro = "t.message_id >= ?" + ("" if ate is None else " AND t.message_id < ?")
        parametros = (desde,) if ate is None else (desde, ate)
        return self.con.execute(
            "SELECT m.titulo, m.artista, COUNT(*) AS c "
            "FROM tocadas t JOIN musicas m ON m.id = t.musica_id "
            f"WHERE {filtro} "
            "GROUP BY m.id ORDER BY c DESC, m.titulo COLLATE NOCASE",
            parametros,
        ).fetchall()

    def ranking_artistas(self, desde=0, ate=None):
        """[(artista, musicas_diferentes, vezes_tocadas), ...] por nº de músicas ÚNICAS."""
        filtro = "t.message_id >= ?" + ("" if ate is None else " AND t.message_id < ?")
        parametros = (desde,) if ate is None else (desde, ate)
        return self.con.execute(
            "SELECT MIN(m.artista) AS artista, "
            "       COUNT(DISTINCT m.id) AS musicas, "
            "       COUNT(t.message_id) AS tocadas "
            "FROM musicas m JOIN tocadas t ON t.musica_id = m.id "
            f"WHERE m.artista_chave <> '' AND {filtro} "
            "GROUP BY m.artista_chave "
            "ORDER BY musicas DESC, tocadas DESC, artista COLLATE NOCASE",
            parametros,
        ).fetchall()

    def ranking_usuarios(self, desde=0):
        """[(usuario_id, pedidos), ...] de quem mais pediu pra quem menos pediu."""
        return self.con.execute(
            "SELECT usuario_id, COUNT(*) AS c FROM pedidos WHERE message_id >= ? "
            "GROUP BY usuario_id ORDER BY c DESC, usuario_id",
            (desde,),
        ).fetchall()

    def pedidos_do_usuario(self, usuario_id, desde=0):
        """[(message_id, conteudo), ...] dos pedidos da pessoa, do mais antigo pro mais novo."""
        return self.con.execute(
            "SELECT message_id, conteudo FROM pedidos "
            "WHERE usuario_id = ? AND message_id >= ? ORDER BY message_id",
            (usuario_id, desde),
        ).fetchall()

    def musica_aleatoria(self):
        """(titulo, artista, vezes_tocada) de uma música sorteada do histórico, ou None."""
        return self.con.execute(
            "SELECT m.titulo, m.artista, COUNT(t.message_id) "
            "FROM musicas m JOIN tocadas t ON t.musica_id = m.id "
            "GROUP BY m.id ORDER BY RANDOM() LIMIT 1"
        ).fetchone()

    def musica_top_do_artista(self, artista: str, desde=0):
        """(titulo, artista) da música mais tocada desse artista (usada pra pegar a capa)."""
        return self.con.execute(
            "SELECT m.titulo, m.artista FROM musicas m JOIN tocadas t ON t.musica_id = m.id "
            "WHERE m.artista_chave = ? AND t.message_id >= ? "
            "GROUP BY m.id ORDER BY COUNT(*) DESC LIMIT 1",
            (normalizar_artista(artista), desde),
        ).fetchone()

    def cache_deezer(self, chave: str):
        """(capa, genero, buscado_em) guardados pra essa chave, ou None."""
        return self.con.execute(
            "SELECT capa, genero, buscado_em FROM deezer_cache WHERE chave = ?", (chave,)
        ).fetchone()

    def salvar_cache_deezer(self, chave: str, capa: str, genero: str, quando: int):
        self.con.execute(
            "INSERT INTO deezer_cache (chave, capa, genero, buscado_em) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(chave) DO UPDATE SET capa = excluded.capa, genero = excluded.genero, "
            "buscado_em = excluded.buscado_em",
            (chave, capa, genero, quando),
        )
        self.con.commit()

    def cache_link(self, chave: str):
        """(nome, buscado_em) guardados pra esse link, ou None."""
        return self.con.execute(
            "SELECT nome, buscado_em FROM links_cache WHERE chave = ?", (chave,)
        ).fetchone()

    def salvar_cache_link(self, chave: str, nome: str, quando: int):
        self.con.execute(
            "INSERT INTO links_cache (chave, nome, buscado_em) VALUES (?, ?, ?) "
            "ON CONFLICT(chave) DO UPDATE SET nome = excluded.nome, buscado_em = excluded.buscado_em",
            (chave, nome, quando),
        )
        self.con.commit()

    def historico_tocadas(self):
        """[(message_id, titulo, artista, bot, channel_id), ...] em ordem cronológica."""
        return self.con.execute(
            "SELECT t.message_id, m.titulo, m.artista, t.bot, t.channel_id "
            "FROM tocadas t JOIN musicas m ON m.id = t.musica_id ORDER BY t.message_id"
        ).fetchall()

    def historico_pedidos(self):
        """[(message_id, usuario_id, conteudo, channel_id), ...] em ordem cronológica."""
        return self.con.execute(
            "SELECT message_id, usuario_id, conteudo, channel_id FROM pedidos ORDER BY message_id"
        ).fetchall()


class Banco(BancoLegado):
    """Estende o esquema original sem apagar tabelas nem recriar o histórico."""

    FONTES = {
        "mensagens": ("mensagens", "autor_id", "mensagens", "ultima_msg"),
        "pedidos": ("pedidos", "usuario_id", "pedidos", "ultimo_pedido"),
        "mudae": ("mudae", "usuario_id", "roletadas", "ultima_roletada"),
    }

    def __init__(self, caminho):
        self.caminho = Path(caminho)
        versao = 0
        if self.caminho.exists():
            with closing(sqlite3.connect(caminho)) as antes:
                versao = antes.execute("PRAGMA user_version").fetchone()[0]
                colunas = [r[1] for r in antes.execute("PRAGMA table_info(tocadas)")]
            if versao < 3:  # 3: resumos, marcos de nível e cor do perfil (todas mudanças aditivas)
                backup_antes_migracao(caminho)
            if colunas and "musica_id" not in colunas:
                raise RuntimeError(
                    "Banco muito antigo: preservado no backup; a migração precisa ser revisada. Nenhuma tabela foi apagada."
                )
        super().__init__(caminho)
        self.con.execute("PRAGMA journal_mode=WAL")
        # Em WAL, NORMAL não faz fsync a cada commit: o banco continua íntegro após uma queda
        # (só as últimas transações podem se perder, e o histórico é relido do Discord).
        self.con.execute("PRAGMA synchronous=NORMAL")
        self.con.execute("PRAGMA busy_timeout=5000")
        self.con.create_function("sem_acento", 1, sem_acento, deterministic=True)
        self.con.create_function("chave_deezer", 1, chave_deezer, deterministic=True)
        self.con.executescript("""
            CREATE TABLE IF NOT EXISTS mensagens (
                message_id INTEGER PRIMARY KEY, autor_id INTEGER NOT NULL,
                canal_id INTEGER NOT NULL, eh_bot INTEGER NOT NULL);
            CREATE INDEX IF NOT EXISTS idx_mensagens_autor ON mensagens(autor_id, message_id);
            CREATE TABLE IF NOT EXISTS mudae (message_id INTEGER PRIMARY KEY, usuario_id INTEGER NOT NULL);
            CREATE INDEX IF NOT EXISTS idx_mudae_usuario ON mudae(usuario_id, message_id);
            CREATE INDEX IF NOT EXISTS idx_pedidos_usuario ON pedidos(usuario_id, message_id);
            CREATE TABLE IF NOT EXISTS autores (
                usuario_id INTEGER PRIMARY KEY, eh_bot INTEGER NOT NULL DEFAULT 0, nome TEXT NOT NULL DEFAULT '');
            CREATE TABLE IF NOT EXISTS totais (
                usuario_id INTEGER PRIMARY KEY,
                mensagens INTEGER NOT NULL DEFAULT 0, ultima_msg INTEGER NOT NULL DEFAULT 0,
                pedidos INTEGER NOT NULL DEFAULT 0, ultimo_pedido INTEGER NOT NULL DEFAULT 0,
                roletadas INTEGER NOT NULL DEFAULT 0, ultima_roletada INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS perfil_usuario (
                usuario_id INTEGER PRIMARY KEY, frase TEXT NOT NULL DEFAULT '',
                favorita TEXT NOT NULL DEFAULT '', capa TEXT NOT NULL DEFAULT '',
                titulo TEXT NOT NULL DEFAULT '', cor INTEGER);
            CREATE TABLE IF NOT EXISTS posses (
                usuario_id INTEGER NOT NULL, item TEXT NOT NULL, detalhe TEXT NOT NULL DEFAULT '',
                titulo INTEGER NOT NULL, insignia INTEGER NOT NULL, desbloqueado_em INTEGER NOT NULL,
                PRIMARY KEY (usuario_id, item, detalhe));
            CREATE TABLE IF NOT EXISTS periodos_fechados (
                tipo TEXT NOT NULL, periodo TEXT NOT NULL, fechado_em INTEGER NOT NULL,
                PRIMARY KEY (tipo, periodo));
            CREATE TABLE IF NOT EXISTS resumos (
                tipo TEXT NOT NULL, periodo TEXT NOT NULL, dados TEXT NOT NULL,
                estado TEXT NOT NULL DEFAULT 'pendente', PRIMARY KEY (tipo, periodo));
            CREATE TABLE IF NOT EXISTS niveis_marco (
                usuario_id INTEGER PRIMARY KEY, marco INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS avisos_nivel (
                id INTEGER PRIMARY KEY AUTOINCREMENT, usuario_id INTEGER NOT NULL,
                marco INTEGER NOT NULL, estado TEXT NOT NULL DEFAULT 'pendente',
                UNIQUE (usuario_id, marco));
            CREATE TABLE IF NOT EXISTS estado (chave TEXT PRIMARY KEY, valor TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS progresso_mensagens (
                channel_id INTEGER PRIMARY KEY, ultimo_msg_id INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS avisos (
                id INTEGER PRIMARY KEY AUTOINCREMENT, chave TEXT NOT NULL UNIQUE,
                usuario_id INTEGER NOT NULL, item TEXT NOT NULL, titulo INTEGER NOT NULL,
                insignia INTEGER NOT NULL, detalhe TEXT NOT NULL DEFAULT '',
                estado TEXT NOT NULL DEFAULT 'pendente');
            CREATE TABLE IF NOT EXISTS classificacao_pedidos (
                chave TEXT PRIMARY KEY, texto TEXT NOT NULL, nome TEXT NOT NULL DEFAULT '',
                genero TEXT NOT NULL DEFAULT '', consultado_em INTEGER NOT NULL DEFAULT 0);
        """)
        if "cor" not in [r[1] for r in self.con.execute("PRAGMA table_info(perfil_usuario)")]:
            self.con.execute("ALTER TABLE perfil_usuario ADD COLUMN cor INTEGER")
        if versao < 2:
            with self.con:
                self.con.execute(
                    "INSERT OR IGNORE INTO autores(usuario_id, eh_bot) SELECT autor_id, eh_bot FROM atividade"
                )
                self.con.execute(
                    "INSERT OR IGNORE INTO autores(usuario_id) SELECT DISTINCT usuario_id FROM pedidos"
                )
                for fonte, (tabela, autor, contagem, ultimo) in self.FONTES.items():
                    for uid, n, mid in self.con.execute(
                        f"SELECT {autor}, COUNT(*), MAX(message_id) FROM {tabela} GROUP BY {autor}"
                    ).fetchall():
                        self.con.execute(
                            "INSERT OR IGNORE INTO totais(usuario_id) VALUES (?)", (uid,)
                        )
                        self.con.execute(
                            f"UPDATE totais SET {contagem}=?, {ultimo}=? WHERE usuario_id=?",
                            (n, mid, uid),
                        )
                for (conteudo,) in self.con.execute(
                    "SELECT DISTINCT conteudo FROM pedidos WHERE conteudo IS NOT NULL"
                ).fetchall():
                    self._registrar_consulta(conteudo)
                self.con.execute("PRAGMA user_version=2")
        if versao < 3:
            with self.con:
                self.con.execute("PRAGMA user_version=3")

    def estado(self, chave, padrao=""):
        row = self.con.execute("SELECT valor FROM estado WHERE chave=?", (chave,)).fetchone()
        return row[0] if row else padrao

    def definir_estado(self, chave, valor):
        with self.con:
            self.con.execute(
                "INSERT INTO estado VALUES (?,?) ON CONFLICT(chave) DO UPDATE SET valor=excluded.valor",
                (chave, str(valor)),
            )

    def marca(self, channel_id):
        row = self.con.execute(
            "SELECT ultimo_msg_id FROM progresso_mensagens WHERE channel_id=?", (channel_id,)
        ).fetchone()
        return row[0] if row else 0

    def _marca(self, channel_id, msg_id):
        self.con.execute(
            "INSERT INTO progresso_mensagens VALUES (?,?) ON CONFLICT(channel_id) DO UPDATE "
            "SET ultimo_msg_id=MAX(ultimo_msg_id, excluded.ultimo_msg_id)",
            (channel_id, msg_id),
        )

    def salvar_marca(self, channel_id, msg_id):
        with self.con:
            self._marca(channel_id, msg_id)

    def _registrar_consulta(self, conteudo):
        consulta = consulta_musical(conteudo)
        if consulta:
            self.con.execute(
                "INSERT OR IGNORE INTO classificacao_pedidos(chave,texto) VALUES (?,?)", consulta
            )

    def _incrementar(self, uid, fonte, mid):
        _, _, contador, ultimo = self.FONTES[fonte]
        self.con.execute("INSERT OR IGNORE INTO totais(usuario_id) VALUES (?)", (uid,))
        self.con.execute(
            f"UPDATE totais SET {contador}={contador}+1,{ultimo}=MAX({ultimo},?) WHERE usuario_id=?",
            (mid, uid),
        )

    def receber(self, msg, *, avancar=False, recompensar=False, avisar=False, elegiveis=None):
        """Mensagem, seus eventos e recompensas são salvos na mesma transação."""
        with self.con:
            uid, mid = msg.author.id, msg.id
            self.con.execute(
                "INSERT INTO autores VALUES (?,?,?) ON CONFLICT(usuario_id) DO UPDATE "
                "SET eh_bot=excluded.eh_bot,nome=excluded.nome",
                (uid, int(msg.author.bot), msg.author.display_name),
            )
            nova = self.con.execute(
                "INSERT OR IGNORE INTO mensagens VALUES (?,?,?,?)",
                (mid, uid, msg.channel.id, int(msg.author.bot)),
            ).rowcount
            if nova:
                self._incrementar(uid, "mensagens", mid)
            pedido_novo = 0
            if eh_pedido(msg):
                conteudo = (msg.content or "")[:2000]
                pedido_novo = self.con.execute(
                    "INSERT OR IGNORE INTO pedidos VALUES (?,?,?,?)",
                    (mid, msg.channel.id, uid, conteudo),
                ).rowcount
                self.con.execute(
                    "UPDATE pedidos SET conteudo=? WHERE message_id=? AND (conteudo IS NULL OR conteudo='')",
                    (conteudo, mid),
                )
                self._registrar_consulta(conteudo)
                if pedido_novo:
                    self._incrementar(uid, "pedidos", mid)
            if eh_mudae(msg):
                if self.con.execute(
                    "INSERT OR IGNORE INTO mudae VALUES (?,?)", (mid, uid)
                ).rowcount:
                    self._incrementar(uid, "mudae", mid)
            musica = extrair_musica(msg)
            if musica:
                t, a = musica
                chave = chave_musica(t, a)
                self.con.execute(
                    "INSERT OR IGNORE INTO musicas(chave,titulo,artista,artista_chave) VALUES (?,?,?,?)",
                    (chave, t, a, normalizar_artista(a)),
                )
                musica_id = self.con.execute(
                    "SELECT id FROM musicas WHERE chave=?", (chave,)
                ).fetchone()[0]
                self.con.execute(
                    "INSERT OR IGNORE INTO tocadas VALUES (?,?,?,?)",
                    (mid, msg.channel.id, musica_id, msg.author.name),
                )
            if avancar:
                self._marca(msg.channel.id, mid)
            if recompensar and not msg.author.bot:
                self._avaliar_usuario(uid, avisar)
                self._rotativos(avisar, str(mid), elegiveis)
        return bool(nova), bool(pedido_novo)

    def _consulta_ranking(self, fonte, desde=0, ate=None, eh_bot=None):
        """SQL (já ordenado) e parâmetros de um ranking de contagens por pessoa."""
        tabela, autor, contador, ultimo = self.FONTES[fonte]
        parametros = []
        if not desde and ate is None:
            sql = f"SELECT t.usuario_id, t.{contador}, t.{ultimo} FROM totais t LEFT JOIN autores a ON a.usuario_id=t.usuario_id WHERE t.{contador}>0"
            ordem = f" ORDER BY t.{contador} DESC,t.{ultimo},t.usuario_id"
        else:
            sql = f"SELECT t.{autor},COUNT(*),MAX(t.message_id) FROM {tabela} t LEFT JOIN autores a ON a.usuario_id=t.{autor} WHERE t.message_id>=?"
            parametros.append(desde)
            if ate is not None:
                sql += " AND t.message_id<?"
                parametros.append(ate)
            ordem = f" GROUP BY t.{autor} ORDER BY COUNT(*) DESC,MAX(t.message_id),t.{autor}"
        if eh_bot is not None:
            sql += " AND COALESCE(a.eh_bot,0)=?"
            parametros.append(int(eh_bot))
        return sql + ordem, parametros

    def ranking_contagens(self, fonte, desde=0, ate=None, eh_bot=None, elegiveis=None):
        sql, parametros = self._consulta_ranking(fonte, desde, ate, eh_bot)
        return [
            (uid, n)
            for uid, n, _ in self.con.execute(sql, parametros)
            if elegiveis is None or uid in elegiveis
        ]

    def lider(self, fonte, eh_bot=None, elegiveis=None):
        """Quem ocupa o 1º lugar do total histórico (ou None), sem montar o ranking inteiro."""
        sql, parametros = self._consulta_ranking(fonte, eh_bot=eh_bot)
        for uid, _, _ in self.con.execute(sql, parametros):
            if elegiveis is None or uid in elegiveis:
                return uid
        return None

    def ranking_usuarios(self, desde=0, elegiveis=None):
        return self.ranking_contagens("pedidos", desde, eh_bot=False, elegiveis=elegiveis)

    def ranking_atividade(self, eh_bot=None, desde=0):
        return self.ranking_contagens("mensagens", desde, eh_bot=eh_bot)

    def total_usuario(self, uid, fonte, desde=0):
        tabela, autor, contador, _ = self.FONTES[fonte]
        if not desde:
            row = self.con.execute(
                f"SELECT {contador} FROM totais WHERE usuario_id=?", (uid,)
            ).fetchone()
            return row[0] if row else 0
        return self.con.execute(
            f"SELECT COUNT(*) FROM {tabela} WHERE {autor}=? AND message_id>=?", (uid, desde)
        ).fetchone()[0]

    def ranking_generos(self, desde=0, genero=None):
        sql = "FROM tocadas t JOIN musicas m ON m.id=t.musica_id LEFT JOIN deezer_cache d ON d.chave=chave_deezer(m.titulo||' '||m.artista) WHERE t.message_id>=?"
        if genero is not None:
            return self.con.execute(
                "SELECT m.titulo,m.artista,COUNT(*) "
                + sql
                + " AND sem_acento(COALESCE(d.genero,''))=? GROUP BY m.id ORDER BY COUNT(*) DESC,MAX(t.message_id),m.id",
                (desde, sem_acento(genero)),
            ).fetchall()
        return self.con.execute(
            "SELECT MIN(d.genero),COUNT(*),COUNT(DISTINCT m.id) "
            + sql
            + " AND COALESCE(d.genero,'')<>'' GROUP BY sem_acento(d.genero) ORDER BY COUNT(*) DESC,MIN(d.genero)",
            (desde,),
        ).fetchall()

    def generos_pendentes(self, desde=0):
        return self.con.execute(
            "SELECT COUNT(DISTINCT m.id) FROM musicas m JOIN tocadas t ON t.musica_id=m.id LEFT JOIN deezer_cache d ON d.chave=chave_deezer(m.titulo||' '||m.artista) WHERE t.message_id>=? AND COALESCE(d.genero,'')=''",
            (desde,),
        ).fetchone()[0]

    def consultas_usuario(self, uid):
        contagem, exemplos = Counter(), {}
        for _, conteudo in self.pedidos_do_usuario(uid):
            consulta = consulta_musical(conteudo)
            if consulta:
                chave, texto = consulta
                contagem[chave] += 1
                exemplos.setdefault(chave, texto)
        return contagem, exemplos

    def perfil(self, uid):
        row = self.con.execute(
            "SELECT frase,favorita,capa,titulo,cor FROM perfil_usuario WHERE usuario_id=?", (uid,)
        ).fetchone()
        return dict(zip(("frase", "favorita", "capa", "titulo", "cor"), row or ("",) * 4 + (None,)))

    def salvar_perfil(self, uid, **campos):
        if not set(campos) <= {"frase", "favorita", "capa", "titulo", "cor"}:
            raise ValueError("Campo de perfil inválido")
        with self.con:
            self.con.execute("INSERT OR IGNORE INTO perfil_usuario(usuario_id) VALUES (?)", (uid,))
            for campo, valor in campos.items():
                self.con.execute(
                    f"UPDATE perfil_usuario SET {campo}=? WHERE usuario_id=?", (valor, uid)
                )

    def itens(self, uid, tipo):
        if tipo not in ("titulo", "insignia"):
            raise ValueError("Tipo inválido")
        return [
            (item, n)
            for item, n in self.con.execute(
                f"SELECT item,COUNT(*) FROM posses WHERE usuario_id=? AND {tipo}=1 GROUP BY item ORDER BY MIN(desbloqueado_em),item",
                (uid,),
            )
            if item in CATALOGO
        ]

    def selecionar_titulo(self, uid, nome):
        for item, _ in self.itens(uid, "titulo"):
            if sem_acento(CATALOGO[item]["nome"]) == sem_acento(nome):
                self.salvar_perfil(uid, titulo=item)
                return True
        return False

    def _conceder(self, uid, item, detalhe="", avisar=False, chave_aviso=None, tipo=None):
        info = CATALOGO[item]
        titulo, insignia = int(info["titulo"]), int(info["insignia"])
        if tipo and not info.get("vinculado"):
            titulo, insignia = int(tipo == "titulo"), int(tipo == "insignia")
        novo = self.con.execute(
            "INSERT OR IGNORE INTO posses VALUES (?,?,?,?,?,?)",
            (uid, item, detalhe, titulo, insignia, int(time.time())),
        ).rowcount
        if not novo:
            return False
        if avisar:
            self.con.execute(
                "INSERT OR IGNORE INTO avisos(chave,usuario_id,item,titulo,insignia,detalhe) VALUES (?,?,?,?,?,?)",
                (chave_aviso or f"{uid}:{item}:{detalhe}", uid, item, titulo, insignia, detalhe),
            )
        return True

    def conceder_manual(self, uid, tipo, nome, eh_bot=False):
        tipo = sem_acento(tipo)
        if tipo not in ("titulo", "insignia") or eh_bot:
            raise ValueError("Escolha titulo ou insignia e uma pessoa.")
        item = next(
            (
                k
                for k, v in CATALOGO.items()
                if v.get(tipo)
                and sem_acento(nome)
                in {
                    sem_acento(k),
                    sem_acento(v["nome"]),
                    sem_acento(v.get("nome_insignia", v["nome"])),
                }
            ),
            None,
        )
        if item is None:
            raise ValueError(
                "Item inexistente. Válidos: "
                + ", ".join(
                    v.get("nome_insignia", v["nome"]) if tipo == "insignia" else v["nome"]
                    for v in CATALOGO.values()
                    if v.get(tipo)
                )
            )
        with self.con:
            if any(k == item for k, _ in self.itens(uid, tipo)):
                return False
            # Uma única posse por item manual, com possibilidade de completar o outro tipo.
            row = self.con.execute(
                "SELECT 1 FROM posses WHERE usuario_id=? AND item=? AND detalhe='manual'",
                (uid, item),
            ).fetchone()
            if row:
                self.con.execute(
                    f"UPDATE posses SET {tipo}=1 WHERE usuario_id=? AND item=? AND detalhe='manual'",
                    (uid, item),
                )
                return True
            return self._conceder(uid, item, "manual", tipo=tipo)

    def _avaliar_usuario(self, uid, avisar):
        autor = self.con.execute("SELECT eh_bot FROM autores WHERE usuario_id=?", (uid,)).fetchone()
        if autor and autor[0]:
            return
        marco = nivel(self.total_usuario(uid, "mensagens")) // 10
        guardado = self.con.execute(
            "SELECT marco FROM niveis_marco WHERE usuario_id=?", (uid,)
        ).fetchone()
        if guardado is None or marco > guardado[0]:
            self.con.execute(
                "INSERT INTO niveis_marco(usuario_id, marco) VALUES (?,?) "
                "ON CONFLICT(usuario_id) DO UPDATE SET marco=excluded.marco",
                (uid, marco),
            )
            # A primeira avaliação só grava (evita avisar marcos antigos ao atualizar o bot).
            if avisar and guardado is not None and marco > 0:
                # Recuperação offline reaplica mensagem por mensagem: fica só o marco mais alto.
                self.con.execute(
                    "DELETE FROM avisos_nivel WHERE usuario_id=? AND estado='pendente' AND marco<?",
                    (uid, marco),
                )
                self.con.execute(
                    "INSERT OR IGNORE INTO avisos_nivel(usuario_id, marco) VALUES (?,?)",
                    (uid, marco),
                )
        for fonte, limites in (("mensagens", TITULOS_MENSAGENS), ("pedidos", TITULOS_MUSICAS)):
            n = self.total_usuario(uid, fonte)
            for limite, item, _ in limites:
                if n >= limite:
                    self._conceder(uid, item, avisar=avisar)
        if self.total_usuario(uid, "mudae") >= 1000:
            self._conceder(uid, "roletador", avisar=avisar)

    def _rotativos(self, avisar, evento, elegiveis=None):
        for fonte, item in (("mensagens", "tagarela_chat"), ("pedidos", "dj_call")):
            vencedor = self.lider(fonte, eh_bot=False, elegiveis=elegiveis)
            antigos = [
                r[0]
                for r in self.con.execute(
                    "SELECT DISTINCT usuario_id FROM posses WHERE item=?", (item,)
                )
            ]
            for uid in antigos:
                if uid != vencedor:
                    self.con.execute(
                        "DELETE FROM posses WHERE usuario_id=? AND item=?", (uid, item)
                    )
                    self.con.execute(
                        "UPDATE perfil_usuario SET titulo='' WHERE usuario_id=? AND titulo=?",
                        (uid, item),
                    )
            if vencedor is not None and vencedor not in antigos:
                self._conceder(
                    vencedor, item, "geral", avisar, f"rotativo:{item}:{vencedor}:{evento}"
                )

    def reconciliar(self, *, avisar=False, elegiveis=None):
        with self.con:
            for (uid,) in self.con.execute(
                "SELECT usuario_id FROM autores WHERE eh_bot=0"
            ).fetchall():
                self._avaliar_usuario(uid, avisar)
            self._rotativos(avisar, str(time.time_ns()), elegiveis)

    def resumo_periodo(self, tipo, periodo, elegiveis=None):
        """Números do período fechado, prontos para virar JSON (só pessoas, sem bots)."""
        desde, ate = limites_periodo(tipo, periodo)
        totais = {}
        for fonte in ("mensagens", "pedidos"):
            tabela, autor, _, _ = self.FONTES[fonte]
            totais[fonte] = self.con.execute(
                f"SELECT COUNT(*) FROM {tabela} t LEFT JOIN autores a ON a.usuario_id=t.{autor} "
                "WHERE t.message_id>=? AND t.message_id<? AND COALESCE(a.eh_bot,0)=0",
                (desde, ate),
            ).fetchone()[0]

        def topo(fonte):
            linhas = self.ranking_contagens(fonte, desde, ate, False, elegiveis)
            return [[uid, n] for uid, n in linhas[:3]]

        return {
            "djs": topo("pedidos"),
            "tagarelas": topo("mensagens"),
            "mensagens": totais["mensagens"],
            "pedidos": totais["pedidos"],
            "musicas": [[t, a, q] for t, a, q in self.ranking_musicas(desde, ate)[:5]],
            "artistas": [[a, n, q] for a, n, q in self.ranking_artistas(desde, ate)[:5]],
        }

    def _guardar_resumo(self, tipo, periodo, elegiveis=None):
        dados = self.resumo_periodo(tipo, periodo, elegiveis)
        if not (dados["mensagens"] or dados["pedidos"]):
            return
        self.con.execute(
            "INSERT OR IGNORE INTO resumos(tipo, periodo, dados) VALUES (?,?,?)",
            (tipo, periodo, json.dumps(dados, ensure_ascii=False)),
        )

    def resumos_pendentes(self, limite=5):
        """[(tipo, periodo, dados)] a enviar: por ano, o mês antes do ano, depois o período."""
        return [
            (tipo, periodo, json.loads(dados))
            for tipo, periodo, dados in self.con.execute(
                "SELECT tipo, periodo, dados FROM resumos WHERE estado='pendente' "
                "ORDER BY substr(periodo,1,4), tipo='ano', periodo LIMIT ?",
                (limite,),
            ).fetchall()
        ]

    def marcar_resumo(self, tipo, periodo, estado):
        with self.con:
            self.con.execute(
                "UPDATE resumos SET estado=? WHERE tipo=? AND periodo=?", (estado, tipo, periodo)
            )

    def fechar_periodos(self, agora=None, *, avisar=False, elegiveis=None):
        agora = (agora or datetime.now(FUSO)).astimezone(FUSO)
        # strftime em SQLite evita converter cada snowflake no Python.
        datas = [
            r[0]
            for r in self.con.execute(
                "SELECT DISTINCT strftime('%Y-%m', ((message_id >> 22)+1420070400000)/1000, 'unixepoch','-3 hours') FROM mensagens UNION SELECT DISTINCT strftime('%Y-%m', ((message_id >> 22)+1420070400000)/1000, 'unixepoch','-3 hours') FROM pedidos"
            )
        ]
        periodos = [("mes", p) for p in datas if p and p < agora.strftime("%Y-%m")]
        periodos += [
            ("ano", p) for p in sorted({p[:4] for p in datas if p and p[:4] < str(agora.year)})
        ]
        fechados = []
        for tipo, p in sorted(periodos, key=lambda x: limites_periodo(*x)[1]):
            with self.con:
                if self.con.execute(
                    "SELECT 1 FROM periodos_fechados WHERE tipo=? AND periodo=?", (tipo, p)
                ).fetchone():
                    continue
                desde, ate = limites_periodo(tipo, p)
                for fonte, prefixo in (("mensagens", "tagarela"), ("pedidos", "dj")):
                    rank = self.ranking_contagens(fonte, desde, ate, False, elegiveis)
                    if rank:
                        self._conceder(rank[0][0], prefixo + "_" + tipo, p, avisar)
                if avisar:
                    self._guardar_resumo(tipo, p, elegiveis)
                self.con.execute(
                    "INSERT INTO periodos_fechados VALUES (?,?,?)", (tipo, p, int(time.time()))
                )
                fechados.append((tipo, p))
        return fechados

    def avisos_nivel_pendentes(self, limite=10):
        return self.con.execute(
            "SELECT id, usuario_id, marco FROM avisos_nivel WHERE estado='pendente' "
            "ORDER BY id LIMIT ?",
            (limite,),
        ).fetchall()

    def marcar_aviso_nivel(self, aviso_id, estado):
        with self.con:
            self.con.execute("UPDATE avisos_nivel SET estado=? WHERE id=?", (estado, aviso_id))

    def hall(self, tipo):
        """[(periodo, dj_uid, tagarela_uid)] dos períodos fechados, do mais recente ao mais antigo."""
        linhas = {}
        for chave, item in (("dj", f"dj_{tipo}"), ("tagarela", f"tagarela_{tipo}")):
            for uid, periodo in self.con.execute(
                "SELECT usuario_id, detalhe FROM posses WHERE item=? "
                "AND detalhe GLOB '[0-9][0-9][0-9][0-9]*'",
                (item,),
            ):
                linhas.setdefault(periodo, {})[chave] = uid
        return [
            (periodo, valores.get("dj"), valores.get("tagarela"))
            for periodo, valores in sorted(linhas.items(), reverse=True)
        ]

    def backup_diario(self, agora=None, *, isolado=False):
        """Uma cópia comprimida por dia, guardando as 7 mais recentes.
        isolado=True usa uma conexão própria, para rodar em outra thread sem travar o bot."""
        dia = (agora or datetime.now(FUSO)).astimezone(FUSO).strftime("%Y-%m-%d")
        pasta = self.caminho.parent / "backups"
        destino = pasta / f"memi_{dia}.db.gz"
        if not destino.exists():
            if isolado:
                with closing(sqlite3.connect(self.caminho)) as copia:
                    backup_sqlite(copia, destino)
            else:
                backup_sqlite(self.con, destino)
            for velho in sorted(pasta.glob("memi_????-??-??.db.gz"))[:-7]:
                velho.unlink()
        return destino


# ===== Capas e gêneros (Deezer) ============================================
class Deezer:
    """Busca capa do álbum e gênero na API pública do Deezer e guarda no banco (cache)."""

    def __init__(self, banco):
        self.banco = banco
        self.session = None
        self.sem = asyncio.Semaphore(3)  # no máx. 3 pedidos ao mesmo tempo (o Deezer tem limite)

    async def fechar(self):
        if self.session and not self.session.closed:
            await self.session.close()

    def _sessao(self):
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=6),
                headers={"User-Agent": f"{BOT_NAME}/1.0"},
            )
        return self.session

    async def baixar_bytes(self, url, limite=2_000_000):
        """Bytes de uma imagem (capa de álbum), ou None se falhar ou passar de `limite` bytes."""
        if not url:
            return None
        async with self.sem:
            try:
                async with self._sessao().get(url) as resp:
                    if resp.status != 200:
                        return None
                    dados = await resp.content.read(limite + 1)
            except (aiohttp.ClientError, asyncio.TimeoutError):
                return None
        return dados if len(dados) <= limite else None

    async def _get(self, caminho, params=None):
        """JSON da resposta, ou None se deu erro (rede, limite, id inexistente...)."""
        self._sessao()
        async with self.sem:
            try:
                async with self.session.get(f"{DEEZER_API}{caminho}", params=params) as resp:
                    if resp.status != 200:
                        return None
                    dados = await resp.json(content_type=None)
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
                return None
        # o Deezer devolve erros (ex.: limite de pedidos) como {"error": {...}} com status 200
        if not isinstance(dados, dict) or "error" in dados:
            return None
        return dados

    async def _buscar(self, q: str):
        """(capa, genero); ('', '') se não achou; None se deu erro (não vale guardar)."""
        dados = await self._get("/search", {"q": q, "limit": 1})
        if dados is None:
            return None
        itens = dados.get("data") or []
        if not itens:
            return "", ""
        album = itens[0].get("album") or {}
        capa = album.get("cover_medium") or album.get("cover") or ""
        genero = ""
        if album.get("id"):
            # o gênero não vem na busca: fica no álbum
            det = await self._get(f"/album/{album['id']}")
            generos = ((det or {}).get("genres") or {}).get("data") or []
            if generos:
                genero = generos[0].get("name") or ""
        return capa, genero

    async def info(self, texto: str, titulo: str = "", artista: str = ""):
        """(capa, genero) do que foi pedido/tocado. Com título+artista a busca é mais precisa."""
        texto = (texto or "").strip()[:100]
        chave = normalizar(texto)
        if not chave or texto.lower().startswith(("http://", "https://")):
            return "", ""

        guardado = self.banco.cache_deezer(chave)
        agora = int(time.time())
        if guardado and (
            (guardado[0] and guardado[1]) or agora - guardado[2] < DEEZER_REPETIR_APOS
        ):
            return guardado[0], guardado[1]

        achado = None
        if titulo and artista:
            t, a = titulo.replace('"', " "), artista.replace('"', " ")
            achado = await self._buscar(f'artist:"{a}" track:"{t}"')
            if achado == ("", ""):
                achado = await self._buscar(f"{t} {a}")
        else:
            achado = await self._buscar(texto)

        if achado is None:  # erro de rede: não guarda, usa o que já tinha
            return (guardado[0], guardado[1]) if guardado else ("", "")
        self.banco.salvar_cache_deezer(chave, achado[0], achado[1], agora)
        return achado

    async def info_seguro(self, texto: str, titulo: str = "", artista: str = ""):
        """Igual ao info, mas nunca trava nem dá erro: no pior caso volta ('', '') e o embed sai sem capa."""
        try:
            return await asyncio.wait_for(self.info(texto, titulo, artista), DEEZER_ESPERA)
        except Exception:  # noqa: BLE001 - capa é enfeite, não pode derrubar o comando
            return "", ""


# ===== Títulos de links (YouTube / Spotify) ================================
RE_LINK = re.compile(r"https?://[^\s<>]+", re.I)
RE_ID = re.compile(r"[\w-]+")
TIPOS_SPOTIFY = {
    "track": "música",
    "album": "álbum",
    "playlist": "playlist",
    "artist": "artista",
    "episode": "episódio",
    "show": "podcast",
}


def analisar_link(texto: str):
    """(url_canonica, endpoint_oembed, tipo) se o texto tem um link de YouTube/Spotify que dá pra ler;
    senão None. O link é limpo (sem ?si=, idioma /intl-pt/ etc.) pra o cache não se repetir."""
    achado = RE_LINK.search(texto or "")
    if not achado:
        return None
    try:
        u = urlparse(achado.group(0).rstrip(".,;"))
    except ValueError:
        return None
    host = (u.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    partes = [p for p in u.path.split("/") if p]
    params = parse_qs(u.query)

    if host in ("youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be"):
        # Links watch?v=...&list=... também representam uma playlist.
        lista = (params.get("list") or [""])[0]
        if lista and RE_ID.fullmatch(lista):
            return f"https://www.youtube.com/playlist?list={lista}", YOUTUBE_OEMBED, "playlist"
        video = ""
        if host == "youtu.be" and partes:
            video = partes[0]
        elif len(partes) > 1 and partes[0] == "shorts":
            video = partes[1]
        elif partes and partes[0] == "watch":
            video = (params.get("v") or [""])[0]
        if video and RE_ID.fullmatch(video):
            return f"https://www.youtube.com/watch?v={video}", YOUTUBE_OEMBED, ""
        lista = (params.get("list") or [""])[0]
        if partes and partes[0] == "playlist" and lista and RE_ID.fullmatch(lista):
            return f"https://www.youtube.com/playlist?list={lista}", YOUTUBE_OEMBED, "playlist"
        return None

    if host == "open.spotify.com":
        if partes and partes[0].startswith("intl-"):
            partes = partes[1:]
        if len(partes) >= 2 and partes[0] in TIPOS_SPOTIFY and RE_ID.fullmatch(partes[1]):
            return (
                f"https://open.spotify.com/{partes[0]}/{partes[1]}",
                SPOTIFY_OEMBED,
                TIPOS_SPOTIFY[partes[0]],
            )
    return None


def posicao_texto(posicao: int, total: int) -> str:
    """'🥇 #1 de 25' (medalha só pro top 3)."""
    medalha = {1: "🥇 ", 2: "🥈 ", 3: "🥉 "}.get(posicao, "🏅 ")
    return f"{medalha}**#{posicao}** de {total}"


def formatar_consulta(texto: str, achado=None, limite: int = 60) -> str:
    """Como mostrar um pedido: se for link com título conhecido, vira [Título](link); senão o texto em código."""
    if achado:
        nome, url = achado
        nome = nome.replace("[", "(").replace("]", ")")
        if len(nome) > limite:
            nome = nome[: limite - 1] + "…"
        return f"[{nome}]({url})"
    return "`" + " ".join(texto.split()).replace("`", "'")[:limite] + "`"


class Links:
    """Descobre o título de links do YouTube (vídeo/playlist) e Spotify (música/álbum/playlist...)
    pelo oEmbed público e guarda no banco (cache)."""

    def __init__(self, banco):
        self.banco = banco
        self.session = None
        self.sem = asyncio.Semaphore(3)

    async def fechar(self):
        if self.session and not self.session.closed:
            await self.session.close()

    async def _oembed(self, endpoint: str, url: str):
        """dict do oEmbed; {} se o link não existe/é privado; None se foi erro passageiro (não guardar)."""
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=5),
                headers={"User-Agent": f"{BOT_NAME}/1.0"},
            )
        params = {"url": url}
        if endpoint == YOUTUBE_OEMBED:
            params["format"] = "json"
        async with self.sem:
            try:
                async with self.session.get(endpoint, params=params) as resp:
                    if resp.status in (400, 401, 403, 404):
                        return {}
                    if resp.status != 200:
                        return None
                    dados = await resp.json(content_type=None)
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
                return None
        return dados if isinstance(dados, dict) else None

    async def titulo(self, texto: str):
        """(nome, url_limpa) se o texto tem um link conhecido e deu pra descobrir o título; senão None."""
        analise = analisar_link(texto)
        if not analise:
            return None
        url, endpoint, tipo = analise
        agora = int(time.time())
        guardado = self.banco.cache_link(url)
        if guardado and (guardado[0] or agora - guardado[1] < LINK_REPETIR_APOS):
            return (guardado[0], url) if guardado[0] else None

        dados = await self._oembed(endpoint, url)
        if dados is None:  # erro passageiro: não guarda, tenta de novo na próxima
            return None
        nome = " ".join(str(dados.get("title") or "").split())
        if nome and endpoint == YOUTUBE_OEMBED and dados.get("author_name"):
            nome += " · " + " ".join(str(dados["author_name"]).split())
        if nome and tipo:
            nome += f" ({tipo})"
        self.banco.salvar_cache_link(url, nome, agora)
        return (nome, url) if nome else None

    async def titulo_seguro(self, texto: str):
        """Igual ao titulo, mas nunca trava nem dá erro: no pior caso volta None e o link aparece cru."""
        try:
            return await asyncio.wait_for(self.titulo(texto), LINK_ESPERA)
        except Exception:  # noqa: BLE001 - título é enfeite, não pode derrubar o comando
            return None


# ===== Ranking paginado ====================================================
def pode_anexar(canal):
    """True se o bot pode enviar arquivos no canal; sem como saber (DM, canal simulado), assume que
    sim. Sem essa permissão o Discord recusa a mensagem inteira, então o envio cai em texto."""
    try:
        return bool(canal.permissions_for(canal.guild.me).attach_files)
    except AttributeError:
        return True


def indice_do_usuario(linhas, uid):
    """Posição (0-based) de `uid` numa lista de linhas cujo 1º item é o ID; None se não estiver."""
    return next((i for i, linha in enumerate(linhas) if linha[0] == uid), None)


class RankingView(discord.ui.View):
    """Ranking paginado com botões ◀ ▶ (qualquer pessoa pode trocar de página).
    `itens` são textos prontos; a montagem do embed fica em estilo.embed_ranking."""

    def __init__(
        self,
        titulo,
        itens,
        rodape_partes=(),
        capa="",
        *,
        subtitulo="",
        meu_indice=None,
        numerar=True,
    ):
        super().__init__(timeout=180)
        self.titulo = titulo
        self.itens = itens
        self.rodape_partes = (
            (rodape_partes,) if isinstance(rodape_partes, str) else tuple(rodape_partes)
        )
        self.capa = capa
        self.subtitulo = subtitulo
        self.meu_indice = meu_indice
        self.numerar = numerar
        self.pagina = 0
        self.total_paginas = max(1, -(-len(itens) // POR_PAGINA))
        self.message = None
        self._atualizar_botoes()

    def montar_embed(self) -> discord.Embed:
        return embed_ranking(
            self.titulo,
            self.itens,
            pagina=self.pagina,
            por_pagina=POR_PAGINA,
            subtitulo=self.subtitulo,
            rodape_partes=self.rodape_partes,
            capa=self.capa,
            meu_indice=self.meu_indice,
            numerar=self.numerar,
        )

    def _atualizar_botoes(self):
        self.anterior.disabled = self.pagina == 0
        self.proxima.disabled = self.pagina >= self.total_paginas - 1

    @discord.ui.button(label="◀", style=discord.ButtonStyle.secondary)
    async def anterior(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.pagina = max(0, self.pagina - 1)
        self._atualizar_botoes()
        await interaction.response.edit_message(embed=self.montar_embed(), view=self)

    @discord.ui.button(label="▶", style=discord.ButtonStyle.secondary)
    async def proxima(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.pagina = min(self.total_paginas - 1, self.pagina + 1)
        self._atualizar_botoes()
        await interaction.response.edit_message(embed=self.montar_embed(), view=self)

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


# ===== Funcionalidade 1: músicas ===========================================
class Musicas(commands.Cog):
    """Leitura do histórico (só dono) e rankings (todo mundo)."""

    def __init__(self, bot):
        self.bot = bot
        self.banco = Banco(ARQUIVO_BANCO)
        self.deezer, self.links = Deezer(self.banco), Links(self.banco)
        self.lock, self.avisos_lock = asyncio.Lock(), asyncio.Lock()
        self.sincronizados = set()
        self.guild_id = SERVIDOR_ID or int(self.banco.estado("servidor_id", "0"))
        self.recuperando = True
        self.tarefas = []
        self._sync_task = None
        self._sync_solicitada = False
        self._sync_completa_pendente = False
        self._ultimo_sync = 0.0
        self._ultimo_fechamento = None
        self._proximo_fechamento = 0.0
        # Chats que o bot enxerga mas não consegue ler: são pulados, não impedem a importação.
        self.canais_ignorados = set()
        self._ignorados_logados = frozenset()

    def _relogio(self):
        return time.monotonic()

    async def cog_check(self, ctx):
        if ctx.guild is None:
            raise commands.NoPrivateMessage()
        if not self.guild_id:
            raise commands.CheckFailure("O bot ainda está identificando o servidor configurado.")
        if ctx.guild.id != self.guild_id:
            raise commands.CheckFailure("Este bot está configurado para outro servidor.")
        return True

    async def cog_unload(self):
        for tarefa in self.tarefas + ([self._sync_task] if self._sync_task else []):
            tarefa.cancel()
        await asyncio.gather(
            *self.tarefas, *([self._sync_task] if self._sync_task else []), return_exceptions=True
        )
        await self.deezer.fechar()
        await self.links.fechar()
        self.banco.con.close()

    def guild(self):
        return self.bot.get_guild(self.guild_id)

    def elegiveis(self):
        guild = self.guild()
        if self.bot.intents.members and guild and guild.chunked:
            return {m.id for m in guild.members}
        return None

    def nome_pessoa(self, uid):
        """Nome para textos do Discord (sem menção e com markdown escapado)."""
        return discord.utils.escape_markdown(self.nome_puro(uid))

    def nome_puro(self, uid):
        """Apelido atual, ou o último guardado, ou o ID (sem escapes: serve para imagens)."""
        guild = self.guild()
        membro = guild.get_member(uid) if guild else None
        if membro is not None:
            nome = membro.display_name
        else:
            row = self.banco.con.execute(
                "SELECT nome FROM autores WHERE usuario_id=?", (uid,)
            ).fetchone()
            nome = (row[0] if row else "") or str(uid)
        return nome

    def ranking(self, fonte, desde=0, eh_bot=None):
        return self.banco.ranking_contagens(fonte, desde, eh_bot=eh_bot, elegiveis=self.elegiveis())

    async def canais_legiveis(self, *, arquivadas=True):
        """Chats de texto, voz e threads ativas que o bot consegue ler; com `arquivadas`, também
        as threads arquivadas (fóruns inclusive). Chats visíveis sem permissão de histórico não
        são erro: ficam em `canais_ignorados` e o motivo vai para o log."""
        guild = self.guild()
        canais, erros, ignorados = {}, [], set()
        if not guild:
            return [], ["servidor indisponível"]

        def adicionar(canal):
            if not hasattr(canal, "history"):
                return
            perm = canal.permissions_for(guild.me)
            if not perm.view_channel:
                return
            if perm.read_message_history:
                canais[canal.id] = canal
            else:
                ignorados.add(canal.id)

        for canal in guild.channels:
            adicionar(canal)
        for thread in guild.threads:
            adicionar(thread)
        try:
            for thread in await guild.active_threads():
                adicionar(thread)
        except discord.HTTPException:
            erros.append("não foi possível listar threads ativas")
        for pai in guild.channels if arquivadas else ():
            if not hasattr(pai, "archived_threads"):
                continue
            perm = pai.permissions_for(guild.me)
            if not (perm.view_channel and perm.read_message_history):
                continue
            consultas = [{}]
            if isinstance(pai, discord.TextChannel):
                consultas.append({"private": True, "joined": not perm.manage_threads})
            for opcoes in consultas:
                try:
                    async for thread in pai.archived_threads(limit=None, **opcoes):
                        adicionar(thread)
                except discord.Forbidden:
                    ignorados.add(pai.id)
                except discord.HTTPException:
                    erros.append(f"não foi possível listar threads arquivadas em {pai.id}")
        self.canais_ignorados = ignorados
        self._avisar_ignorados()
        return list(canais.values()), erros

    def _avisar_ignorados(self):
        """Registra no log, uma vez por mudança, os chats pulados por falta de permissão."""
        atuais = frozenset(self.canais_ignorados)
        if atuais and atuais != self._ignorados_logados:
            logging.warning(
                "Chats ignorados por falta de permissão de histórico: %s",
                ", ".join(str(cid) for cid in sorted(atuais)),
            )
        self._ignorados_logados = atuais

    def iniciar_sync(self, *, leve=False):
        """Agenda uma leitura. `leve` pula a listagem de threads arquivadas (cara na API); um
        pedido completo feito enquanto isso vence e faz a próxima passagem completa."""
        self._sync_solicitada = True
        self._sync_completa_pendente = self._sync_completa_pendente or not leve
        if self._sync_task is None or self._sync_task.done():

            async def recuperar():
                while self._sync_solicitada:
                    self._sync_solicitada = False
                    completa, self._sync_completa_pendente = self._sync_completa_pendente, False
                    if completa:
                        await self.sincronizar()
                    else:
                        await self.sincronizar(arquivadas=False)
                await self.enviar_avisos()
                await self.enviar_resumos()
                await self.enviar_avisos_nivel()

            self._sync_task = asyncio.create_task(recuperar(), name="memi-sincronizar")

    @commands.Cog.listener()
    async def on_ready(self):
        if not self.guild_id:
            canal = self.bot.get_channel(CANAL_AVISOS)
            if canal and getattr(canal, "guild", None):
                self.guild_id = canal.guild.id
            elif len(self.bot.guilds) == 1:
                self.guild_id = self.bot.guilds[0].id
            else:
                logging.error("Defina MEMI_GUILD_ID: não consegui identificar um único servidor.")
                return
            self.banco.definir_estado("servidor_id", self.guild_id)
        guild = self.guild()
        if self.bot.intents.members and guild and not guild.chunked:
            await guild.chunk(cache=True)
        self.sincronizados.clear()
        self.iniciar_sync()
        if not self.tarefas:
            self.tarefas = [
                asyncio.create_task(self.manter(), name="memi-manutencao"),
                asyncio.create_task(self.classificar(), name="memi-generos"),
            ]

    @commands.Cog.listener()
    async def on_resumed(self):
        self.recuperando = True
        self.sincronizados.clear()
        self.iniciar_sync()

    @commands.Cog.listener()
    async def on_disconnect(self):
        self.recuperando = True
        self.sincronizados.clear()

    @commands.Cog.listener()
    async def on_member_remove(self, member):
        if member.guild.id == self.guild_id:
            async with self.lock:
                if self.banco.estado("importacao_concluida") == "1":
                    self.banco.reconciliar(avisar=True, elegiveis=self.elegiveis())
            await self.enviar_avisos()

    @commands.Cog.listener()
    async def on_member_join(self, member):
        await self.on_member_remove(member)

    @commands.Cog.listener()
    async def on_message(self, msg):
        if msg.guild is None or msg.guild.id != self.guild_id:
            return
        if self.recuperando:
            # Eventos entregues na reconexão esperam o histórico mais antigo.
            # Não inserir o futuro antes de reconstituir o desempate dos Top 1.
            if self._sync_task is None or self._sync_task.done():
                self.iniciar_sync()
            await asyncio.shield(self._sync_task)
        async with self.lock:
            pronto = self.banco.estado("importacao_concluida") == "1" and not self.recuperando
            # Um canal ainda não lido não pode avançar além do histórico offline.
            self.banco.receber(
                msg,
                avancar=msg.channel.id in self.sincronizados,
                recompensar=pronto,
                avisar=pronto,
                elegiveis=self.elegiveis(),
            )
        if pronto:
            await self.enviar_avisos()
        if msg.channel.id not in self.sincronizados and msg.channel.id not in self.canais_ignorados:
            self.iniciar_sync()

    async def sincronizar(
        self, *, completo=False, silencioso=False, status=None, canais=None, arquivadas=True
    ):
        async with self.lock:
            self.recuperando = True
            inicial = self.banco.estado("importacao_concluida") != "1"
            silencioso = silencioso or inicial
            total = 0
            ultima_edicao = 0.0
            try:
                if canais is None:
                    canais, erros = await self.canais_legiveis(arquivadas=arquivadas)
                    global_ = True
                    if not canais:
                        erros.append("nenhum chat com histórico acessível")
                else:
                    erros, global_ = [], False
                corte = discord.utils.time_snowflake(discord.utils.utcnow())
                corte_data = discord.utils.snowflake_time(corte).astimezone(FUSO)
                elegiveis = self.elegiveis()
                heap = []

                async def proxima(canal, it):
                    nonlocal total
                    try:
                        msg = await anext(it)
                    except StopAsyncIteration:
                        # Confirma todo o intervalo somente depois da leitura completa.
                        self.banco.salvar_marca(canal.id, corte - 1)
                        self.sincronizados.add(canal.id)
                    except discord.Forbidden:
                        if canal.id not in self.canais_ignorados:
                            logging.warning(
                                "Sem acesso ao histórico de %s; chat ignorado.", canal.id
                            )
                        self.canais_ignorados.add(canal.id)
                    except discord.HTTPException as exc:
                        erros.append(f"histórico de {canal.id}: {type(exc).__name__}")
                        logging.warning("Falha no histórico do canal %s", canal.id, exc_info=True)
                    else:
                        heapq.heappush(heap, (msg.id, canal.id, msg, canal, it))

                for canal in canais:
                    marca = 0 if completo else self.banco.marca(canal.id)
                    it = canal.history(
                        limit=None,
                        after=discord.Object(id=marca) if marca else None,
                        before=discord.Object(id=corte),
                        oldest_first=True,
                    ).__aiter__()
                    await proxima(canal, it)
                while heap:
                    _, _, msg, canal, it = heapq.heappop(heap)
                    self.banco.receber(
                        msg,
                        avancar=True,
                        recompensar=not silencioso,
                        avisar=not silencioso,
                        elegiveis=elegiveis,
                    )
                    total += 1
                    if status and time.monotonic() - ultima_edicao >= 5:
                        ultima_edicao = time.monotonic()
                        try:
                            await status.edit(
                                content=f"📖 Lendo {len(canais)} chats e threads • {milhar(total)} mensagens verificadas..."
                            )
                        except discord.HTTPException:
                            pass
                    await proxima(canal, it)
                    if total % 200 == 0:
                        await asyncio.sleep(0)
                # Um resultado parcial não pode fechar períodos nem concluir a importação.
                if global_ and not erros:
                    self.banco.reconciliar(avisar=not silencioso, elegiveis=self.elegiveis())
                    # O fechamento considera o instante até o qual o histórico foi lido.
                    self.banco.fechar_periodos(
                        corte_data, avisar=not silencioso, elegiveis=self.elegiveis()
                    )
                    self._ultimo_fechamento = corte_data.strftime("%Y-%m")
                    self.banco.definir_estado("importacao_concluida", "1")
                    self.banco.definir_estado("sincronizacao_completa", "1")
                    with self.banco.con:
                        self.banco.con.execute(
                            "INSERT INTO atividade_scan VALUES (1,?) ON CONFLICT(id) DO UPDATE SET quando=excluded.quando",
                            (int(time.time()),),
                        )
                elif global_:
                    self.banco.definir_estado("sincronizacao_completa", "0")
                elif not erros and not inicial:
                    self.banco.reconciliar(avisar=False, elegiveis=self.elegiveis())
                self._ultimo_sync = self._relogio()
                if erros:
                    logging.warning("Leitura parcial: %s", "; ".join(erros))
                else:
                    logging.info(
                        "Leitura concluída: %s mensagens verificadas em %s chats/threads; silencioso=%s",
                        total,
                        len(canais),
                        silencioso,
                    )
                if status:
                    resumo = f"✅ Leitura concluída: {milhar(total)} mensagens verificadas em {len(canais)} chats/threads. Sem duplicação."
                    if erros:
                        resumo = f"⚠️ Leitura parcial: {milhar(total)} mensagens. {len(erros)} falha(s); detalhes no memi_bot.log. Repita mm!scan depois de resolver o problema."
                    elif silencioso:
                        resumo += " Histórico e conquistas recalculados sem avisos."
                    if self.canais_ignorados:
                        resumo += (
                            f"\nℹ️ {len(self.canais_ignorados)} chat(s) ignorado(s) por falta de"
                            " permissão de histórico (lista no memi_bot.log)."
                        )
                    await status.edit(content=resumo)
                return total, erros
            except Exception:
                self.banco.definir_estado("sincronizacao_completa", "0")
                logging.exception("Falha na sincronização; o progresso confirmado foi preservado.")
                if status:
                    await status.edit(
                        content="⚠️ A leitura foi interrompida. Progresso salvo; veja memi_bot.log e tente novamente."
                    )
                return total, ["sincronização interrompida"]
            finally:
                self.recuperando = False
        # Avisos são enviados pela manutenção, fora do bloqueio do histórico.

    async def enviar_avisos(self):
        if self.recuperando or self.banco.estado("importacao_concluida") != "1":
            return
        async with self.avisos_lock:
            canal = self.bot.get_channel(CANAL_AVISOS)
            if canal is None:
                logging.warning("Canal de avisos %s não está acessível.", CANAL_AVISOS)
                return
            for aid, uid, item, titulo, insignia, detalhe in self.banco.con.execute(
                "SELECT id,usuario_id,item,titulo,insignia,detalhe FROM avisos WHERE estado='pendente' ORDER BY id LIMIT 20"
            ).fetchall():
                info = CATALOGO.get(item)
                if not info:
                    continue
                pessoa = self.guild().get_member(uid) if self.guild() else None
                row = self.banco.con.execute(
                    "SELECT nome FROM autores WHERE usuario_id=?", (uid,)
                ).fetchone()
                nome = discord.utils.escape_markdown(
                    pessoa.display_name if pessoa else (row[0] if row else str(uid))
                )
                tipo = (
                    "Insígnia e Título desbloqueados"
                    if titulo and insignia
                    else "Título desbloqueado" if titulo else "Insígnia desbloqueada"
                )
                texto = f"Parabéns, {nome}!!! {tipo}: {info['nome']}!"
                if detalhe and detalhe not in ("geral", "manual"):
                    texto += f" ({detalhe})"
                # Reserva persistente antes de enviar: evita reenviar em caso de queda após o envio.
                with self.banco.con:
                    self.banco.con.execute(
                        "UPDATE avisos SET estado='reservado' WHERE id=?", (aid,)
                    )
                try:
                    await canal.send(texto, allowed_mentions=discord.AllowedMentions.none())
                except (discord.Forbidden, discord.NotFound):
                    with self.banco.con:
                        self.banco.con.execute(
                            "UPDATE avisos SET estado='pendente' WHERE id=?", (aid,)
                        )
                    logging.warning("Sem permissão para enviar avisos.")
                    return
                except (discord.HTTPException, aiohttp.ClientError, asyncio.TimeoutError):
                    logging.exception(
                        "Resultado incerto no envio do aviso %s; não será repetido.", aid
                    )
                else:
                    with self.banco.con:
                        self.banco.con.execute(
                            "UPDATE avisos SET estado='enviado' WHERE id=?", (aid,)
                        )

    async def _enviar_reservado(self, canal, marcar, descricao, **conteudo):
        """Reserva → envia → confirma. `marcar(estado)` grava o estado no banco.
        Devolve False quando falta permissão (o chamador deve parar e tentar depois)."""
        marcar("reservado")
        try:
            await canal.send(allowed_mentions=discord.AllowedMentions.none(), **conteudo)
        except (discord.Forbidden, discord.NotFound):
            marcar("pendente")
            logging.warning("Sem permissão para enviar %s.", descricao)
            return False
        except aiohttp.ClientConnectorError:
            # A requisição nunca saiu da máquina: é certeza de não entrega, então tenta depois.
            marcar("pendente")
            logging.warning("Sem conexão para enviar %s; tentando de novo depois.", descricao)
            return False
        except (discord.HTTPException, aiohttp.ClientError, asyncio.TimeoutError):
            logging.exception("Resultado incerto no envio de %s; não será repetido.", descricao)
            return False  # rede provavelmente instável: só este item fica em risco, não o lote
        else:
            marcar("enviado")
        return True

    async def enviar_resumos(self):
        """Posta no canal de avisos os resumos de mês/ano pendentes, um embed por resumo."""
        if self.recuperando or self.banco.estado("importacao_concluida") != "1":
            return
        async with self.avisos_lock:
            canal = self.bot.get_channel(CANAL_AVISOS)
            if canal is None:
                return
            for tipo, periodo, dados in self.banco.resumos_pendentes():
                capa_url = ""
                if dados.get("musicas"):
                    titulo, artista, _ = dados["musicas"][0]
                    capa_url, _ = await self.deezer.info_seguro(
                        f"{titulo} {artista}".strip(), titulo, artista
                    )
                arquivo = await self._imagem_do_resumo(tipo, periodo, dados, capa_url, canal)
                embed = embed_resumo(
                    tipo, periodo, dados, self.nome_pessoa, com_imagem=arquivo is not None
                )
                conteudo = {"embed": embed}
                if arquivo is not None:
                    embed.set_image(url="attachment://resumo.png")
                    conteudo["file"] = arquivo
                elif capa_url:
                    embed.set_thumbnail(url=capa_url)
                if self.recuperando:  # uma reconexão pode ter começado durante a busca da capa
                    return
                if not await self._enviar_reservado(
                    canal,
                    lambda estado, t=tipo, p=periodo: self.banco.marcar_resumo(t, p, estado),
                    f"resumo {tipo} {periodo}",
                    **conteudo,
                ):
                    return

    async def _imagem_do_resumo(self, tipo, periodo, dados, capa_url, canal):
        """discord.File com a imagem do resumo, ou None (sem Pillow, sem permissão de anexar ou
        falha): o embed completo assume."""
        if not imagens.disponivel() or not pode_anexar(canal):
            return None
        guild = self.guild()

        def ganhador(chave, um, varios):
            lista = dados.get(chave) or []
            if not lista:
                return None, None
            uid, n = lista[0]
            return uid, (self.nome_puro(uid), plural(n, um, varios))

        uid_dj, dj = ganhador("djs", "pedido", "pedidos")
        uid_tag, tagarela = ganhador("tagarelas", "mensagem", "mensagens")

        async def avatar_de(uid):
            membro = guild.get_member(uid) if guild and uid is not None else None
            return await self.baixar_avatar(membro)

        capa = await self.deezer.baixar_bytes(capa_url)
        titulo = periodo if tipo == "ano" else MESES_EXTENSO[int(periodo[5:7]) - 1].capitalize()
        return await self.gerar_imagem(
            imagens.gerar_resumo,
            "resumo.png",
            {
                "tipo": tipo,
                "titulo": titulo,
                "ano": periodo[:4],
                "dj": dj,
                "tagarela": tagarela,
                "musica": tuple(dados["musicas"][0]) if dados.get("musicas") else None,
                "mensagens": milhar(dados.get("mensagens", 0)),
                "pedidos": milhar(dados.get("pedidos", 0)),
                "cor": None,
            },
            await avatar_de(uid_dj),
            await avatar_de(uid_tag),
            capa,
            canal=canal,
        )

    async def enviar_avisos_nivel(self):
        """Posta no canal de avisos os level ups (a cada 10 níveis) pendentes."""
        if self.recuperando or self.banco.estado("importacao_concluida") != "1":
            return
        async with self.avisos_lock:
            canal = self.bot.get_channel(CANAL_AVISOS)
            if canal is None:
                return
            guild = self.guild()
            for aviso_id, uid, marco in self.banco.avisos_nivel_pendentes():
                pessoa = guild.get_member(uid) if guild else None
                nivel_atual, avanco, meta = progresso_valores(
                    self.banco.total_usuario(uid, "mensagens")
                )
                arquivo = None
                if imagens.disponivel() and pode_anexar(canal):
                    arquivo = await self.gerar_imagem(
                        imagens.gerar_nivel,
                        "nivel.png",
                        {
                            "nome": self.nome_puro(uid),
                            "nome_alt": getattr(pessoa, "name", ""),
                            "marco": marco,
                            "nivel": nivel_atual,
                            "avanco": avanco,
                            "meta": meta,
                            "cor": self.banco.perfil(uid)["cor"],
                        },
                        await self.baixar_avatar(pessoa),
                        canal=canal,
                    )
                avatar_url = pessoa.display_avatar.with_size(128).url if pessoa else ""
                embed = embed_nivel(
                    self.nome_pessoa(uid),
                    marco,
                    nivel_atual,
                    avanco,
                    meta,
                    avatar_url,
                    com_imagem=arquivo is not None,
                )
                conteudo = {"embed": embed}
                if arquivo is not None:
                    embed.set_image(url="attachment://nivel.png")
                    conteudo["file"] = arquivo
                if not await self._enviar_reservado(
                    canal,
                    lambda estado, a=aviso_id: self.banco.marcar_aviso_nivel(a, estado),
                    f"aviso de nível {marco * 10}",
                    **conteudo,
                ):
                    return

    async def ciclo_manutencao(self):
        """Uma passada da manutenção: backup, fechamento de períodos, avisos e leitura de segurança."""
        if self.recuperando:
            return
        # Cópia em outra thread (conexão própria): não trava o bot enquanto comprime o banco.
        await asyncio.to_thread(self.banco.backup_diario, None, isolado=True)
        chave = datetime.now(FUSO).strftime("%Y-%m")
        if (
            chave != self._ultimo_fechamento
            and self.banco.estado("importacao_concluida") == "1"
            and self._relogio() >= self._proximo_fechamento
        ):
            # Antes de fechar, recupera os eventos que possam faltar na virada.
            _, erros = await self.sincronizar()
            if erros:
                # Sem isso, uma falha persistente repetiria a leitura completa a cada ciclo.
                self._proximo_fechamento = self._relogio() + SYNC_REPETIR_APOS_ERRO
            else:
                self._ultimo_fechamento = chave
        await self.enviar_avisos()
        await self.enviar_resumos()
        await self.enviar_avisos_nivel()
        if self._relogio() - self._ultimo_sync > SYNC_PERIODICO:
            self.iniciar_sync(leve=True)

    async def manter(self):
        while not self.bot.is_closed():
            try:
                await self.bot.wait_until_ready()
                await self.ciclo_manutencao()
            except Exception:
                logging.exception("Falha na manutenção; tentando de novo no próximo ciclo.")
            await asyncio.sleep(MANUTENCAO_INTERVALO)

    async def classificar(self):
        while not self.bot.is_closed():
            try:
                await self.bot.wait_until_ready()
                agora = int(time.time())
                faixas = self.banco.con.execute(
                    "SELECT m.id,m.titulo,m.artista FROM musicas m LEFT JOIN deezer_cache d ON d.chave=chave_deezer(m.titulo||' '||m.artista) LEFT JOIN estado e ON e.chave='genero:'||m.id WHERE (d.chave IS NULL OR (d.genero='' AND d.buscado_em<?)) AND CAST(COALESCE(e.valor,'0') AS INTEGER)<? ORDER BY COALESCE(e.valor,'0'),m.id LIMIT 3",
                    (agora - DEEZER_REPETIR_APOS, agora - 3600),
                ).fetchall()
                for mid, t, a in faixas:
                    self.banco.definir_estado(f"genero:{mid}", agora)
                    await self.deezer.info_seguro(f"{t} {a}".strip(), t, a)
                    await asyncio.sleep(1)
                consultas = self.banco.con.execute(
                    "SELECT chave,texto FROM classificacao_pedidos WHERE genero='' AND consultado_em<? ORDER BY consultado_em,chave LIMIT 3",
                    (agora - 3600,),
                ).fetchall()
                for chave, texto in consultas:
                    nome = texto
                    if analisar_link(texto):
                        achado = await self.links.titulo_seguro(texto)
                        nome = achado[0] if achado else ""
                    _, genero = await self.deezer.info_seguro(nome) if nome else ("", "")
                    with self.banco.con:
                        self.banco.con.execute(
                            "UPDATE classificacao_pedidos SET nome=?,genero=?,consultado_em=? WHERE chave=?",
                            (nome, genero, agora, chave),
                        )
                    await asyncio.sleep(1)
            except Exception:
                logging.exception("Falha na classificação de gêneros.")
            await asyncio.sleep(30)

    @commands.command(name="read", hidden=True)
    @commands.guild_only()
    @so_memi()
    async def read(self, ctx, *args):
        completo = any(sem_acento(x) in ("tudo", "completo", "full") for x in args)
        canais = []
        for arg in args:
            if sem_acento(arg) in ("tudo", "completo", "full"):
                continue
            match = re.fullmatch(r"(?:<#)?(\d+)>?", arg)
            canal = ctx.guild.get_channel_or_thread(int(match[1])) if match else None
            if not canal or not hasattr(canal, "history"):
                await responder(
                    ctx, "aviso", "Canal inválido. Use mm!read, mm!read tudo ou mm!read #canal."
                )
                return
            canais.append(canal)
        # Sem canais explícitos, verifica todos os chats, inclusive voz e threads.
        await self.iniciar_leitura(
            ctx, "📖 Começando a leitura...", completo=completo, canais=canais
        )

    async def iniciar_leitura(self, ctx, abertura, *, completo=False, canais=None):
        """Leitura manual do dono (mm!read e mm!scan): silenciosa, uma por vez, com progresso."""
        if self.lock.locked():
            await responder(ctx, "aviso", "Já tem uma leitura em andamento. Aguarde terminar.")
            return
        status = await ctx.send(abertura)
        await self.sincronizar(
            completo=completo, silencioso=True, status=status, canais=canais or None
        )

    async def enviar_ranking(
        self,
        ctx,
        titulo,
        itens,
        *,
        rodape_partes=(),
        capa="",
        subtitulo="",
        meu_indice=None,
        numerar=True,
    ):
        """Envia um ranking paginado; acrescenta o aviso de importação enquanto ela não terminar."""
        partes = list(rodape_partes)
        if self.banco.estado("importacao_concluida") != "1":
            partes.append(f"{EMOJI['importando']} importando histórico")
        view = RankingView(
            titulo, itens, partes, capa, subtitulo=subtitulo, meu_indice=meu_indice, numerar=numerar
        )
        view.message = await ctx.send(
            embed=view.montar_embed(), view=view, allowed_mentions=discord.AllowedMentions.none()
        )

    @commands.command(name="musicas", aliases=["músicas", "ranking"])
    @commands.guild_only()
    async def musicas(self, ctx, *args):
        palavras = [sem_acento(x) for x in args]
        if "semana" in palavras:
            await responder(ctx, "aviso", "Não existe ranking semanal: use mes ou ano.")
            return
        periodos = [x for x in palavras if x in ("mes", "ano")]
        if len(set(periodos)) > 1:
            await responder(ctx, "aviso", "Use um período por vez: mes ou ano.")
            return
        periodo = periodos[0] if periodos else ""
        palavras = [x for x in palavras if x not in ("mes", "ano")]
        genero = None
        if "genero" in palavras or "generos" in palavras:
            indice = next(i for i, x in enumerate(palavras) if x in ("genero", "generos"))
            if indice != 0:
                await responder(ctx, "aviso", "Use mm!musicas genero [NOME] [mes|ano].")
                return
            tipo = "genero"
            genero = " ".join(palavras[1:]) or None
        else:
            tipo = palavras[0] if palavras else ""
            validos = {
                sem_acento(x)
                for x in NOMES_RANKING_MUSICA + NOMES_RANKING_ARTISTA + NOMES_RANKING_USUARIO
            }
            if len(palavras) > 1 or tipo not in validos:
                await responder(
                    ctx, "aviso", "Use mm!musicas [artista|ios|genero [NOME]] [mes|ano]."
                )
                return
        desde, rotulo = intervalo(periodo)
        capa, meu_indice = "", None
        if tipo == "genero":
            linhas = self.banco.ranking_generos(desde, genero)
            if genero:
                titulo = f"🎼 Músicas de {genero}"
                itens = [linha_faixa(t, a, q) for t, a, q in linhas]
            else:
                titulo = "🎼 Ranking de gêneros"
                itens = [f"**{g}** · {q} tocadas · {n} músicas diferentes" for g, q, n in linhas]
            pendentes = self.banco.generos_pendentes(desde)
            partes = (
                f"{pendentes} músicas sem gênero",
                "classificação automática em segundo plano",
            )
        elif tipo in {sem_acento(x) for x in NOMES_RANKING_ARTISTA}:
            linhas = self.banco.ranking_artistas(desde)
            titulo = "🎤 Ranking de artistas"
            itens = [f"**{a[:80]}** · {n} músicas diferentes · {q} tocadas" for a, n, q in linhas]
            partes = (f"{len(linhas)} artistas",)
            if linhas:
                t, a = self.banco.musica_top_do_artista(linhas[0][0], desde)
                capa, _ = await self.deezer.info_seguro(f"{t} {a}".strip(), t, a)
        elif tipo in {sem_acento(x) for x in NOMES_RANKING_USUARIO}:
            linhas = self.ranking("pedidos", desde, False)
            titulo = "🎧 Quem mais pediu música"
            itens = [f"**{self.nome_pessoa(uid)}** · {milhar(n)} pedidos" for uid, n in linhas]
            partes = (f"{len(linhas)} pessoas", f"{milhar(sum(n for _, n in linhas))} pedidos")
            meu_indice = indice_do_usuario(linhas, ctx.author.id)
        else:
            linhas = self.banco.ranking_musicas(desde)
            titulo = "🏆 Ranking de músicas mais tocadas"
            itens = [linha_faixa(t, a, q) for t, a, q in linhas]
            partes = (f"{len(linhas)} músicas", f"{milhar(sum(q for _, _, q in linhas))} tocadas")
            if linhas:
                t, a, _ = linhas[0]
                capa, _ = await self.deezer.info_seguro(f"{t} {a}".strip(), t, a)
        await self.enviar_ranking(
            ctx,
            titulo,
            itens,
            rodape_partes=partes,
            capa=capa,
            subtitulo=rotulo,
            meu_indice=meu_indice,
        )

    async def resumo_musical(self, uid):
        consultas, exemplos = self.banco.consultas_usuario(uid)
        mais = ""
        if consultas:
            chave, n = consultas.most_common(1)[0]
            achado = await self.links.titulo_seguro(exemplos[chave])
            mais = f"{formatar_consulta(exemplos[chave],achado,100)} — {n}x"
        generos = Counter()
        pendentes = 0
        for chave, n in consultas.items():
            row = self.banco.con.execute(
                "SELECT genero FROM classificacao_pedidos WHERE chave=?", (chave,)
            ).fetchone()
            genero = row[0] if row else ""
            if not genero and not analisar_link(exemplos[chave]):
                cache = self.banco.cache_deezer(chave_deezer(exemplos[chave]))
                genero = cache[1] if cache else ""
            if genero:
                generos[genero] += n
            else:
                pendentes += 1
        return mais, (generos.most_common(1)[0][0] if generos else ""), pendentes

    @commands.command(name="perfil")
    @commands.guild_only()
    async def perfil(self, ctx, pessoa: discord.Member = None):
        pessoa = pessoa or ctx.author
        async with ctx.typing():
            mais, genero, pendentes = await self.resumo_musical(pessoa.id)
        paginas = self.paginas_perfil(pessoa, mais, genero, pendentes)
        view = PerfilView(paginas)
        view.message = await ctx.send(
            embed=paginas[0], view=view, allowed_mentions=discord.AllowedMentions.none()
        )

    async def baixar_avatar(self, pessoa):
        """Bytes PNG do avatar (256 px), ou None se não houver ou falhar (a imagem usa um círculo)."""
        if pessoa is None:
            return None
        try:
            return await pessoa.display_avatar.replace(size=256, format="png").read()
        except (discord.HTTPException, aiohttp.ClientError, asyncio.TimeoutError, AttributeError):
            return None

    async def gerar_imagem(self, funcao, nome_arquivo, *args, canal=None):
        """discord.File com a imagem gerada em thread, ou None (sem Pillow, sem permissão de anexar
        no `canal`, erro ou tempo esgotado): nesses casos a mensagem sai só com o texto. Cada
        chamada cria um arquivo novo."""
        if not imagens.disponivel() or (canal is not None and not pode_anexar(canal)):
            return None
        try:
            png = await asyncio.wait_for(asyncio.to_thread(funcao, *args), timeout=IMAGEM_TIMEOUT)
        except Exception:  # noqa: BLE001 - a imagem é enfeite; nunca derruba a mensagem
            logging.exception("Falha ao gerar a imagem %s", nome_arquivo)
            return None
        return discord.File(io.BytesIO(png), filename=nome_arquivo)

    def dados_cartao(self, pessoa):
        """Dados do cartão de perfil (texto/números simples; a imagem não desenha emojis)."""
        uid = pessoa.id
        perfil = self.banco.perfil(uid)
        totais = {
            fonte: self.banco.total_usuario(uid, fonte)
            for fonte in ("mensagens", "pedidos", "mudae")
        }
        nivel_atual, avanco, meta = progresso_valores(totais["mensagens"])

        def posicao(fonte):
            linhas = self.ranking(fonte, eh_bot=pessoa.bot if fonte == "mensagens" else False)
            indice = indice_do_usuario(linhas, uid)
            return f"#{indice + 1} de {len(linhas)}" if indice is not None else ""

        return {
            "nome": pessoa.display_name,
            "nome_alt": getattr(pessoa, "name", "") or "",
            "titulo": CATALOGO.get(perfil["titulo"], {}).get("nome", ""),
            "nivel": nivel_atual,
            "avanco": avanco,
            "meta": meta,
            "mensagens": totais["mensagens"],
            "pedidos": totais["pedidos"],
            "roletadas": totais["mudae"],
            "pos_mensagens": posicao("mensagens"),
            "pos_pedidos": posicao("pedidos"),
            "pos_mudae": posicao("mudae"),
            "cor": perfil["cor"],  # None: as imagens usam o coral padrão
        }

    def paginas_perfil(self, pessoa, mais="", genero="", pendentes=0):
        uid = pessoa.id
        perfil = self.banco.perfil(uid)
        fav = CATALOGO.get(perfil["titulo"], {}).get("nome", "")
        nome = (pessoa.display_name + (f" ({fav})" if fav else "")).upper()[:256]
        mes, _ = intervalo("mes")
        ano, _ = intervalo("ano")
        totais = {
            fonte: self.banco.total_usuario(uid, fonte)
            for fonte in ("mensagens", "pedidos", "mudae")
        }
        lvl, avanco, meta = progresso_valores(totais["mensagens"])
        ranks = {}
        for fonte in totais:
            linhas = self.ranking(fonte, eh_bot=pessoa.bot if fonte == "mensagens" else False)
            pos = next((i for i, (u, _) in enumerate(linhas, 1) if u == uid), None)
            ranks[fonte] = posicao_texto(pos, len(linhas)) if pos else ""
        insignias = "  ".join(
            f"{CATALOGO[k]['emoji']}" + (f" x{n}" if n > 1 else "")
            for k, n in self.banco.itens(uid, "insignia")
        )
        cor = perfil["cor"] if perfil["cor"] is not None else COR_PADRAO
        importando = self.banco.estado("importacao_concluida") != "1"

        def pagina(n, *extras):
            e = discord.Embed(title=nome, color=cor)
            e.set_thumbnail(url=pessoa.display_avatar.with_size(128).url)
            aviso = f"{EMOJI['importando']} importando histórico" if importando else ""
            e.set_footer(text=rodape_do_bot(f"{n}/3", *extras, aviso))
            return e

        def nivel_texto():
            if lvl >= 1000:
                return f"{barra(1, 1)} nível máximo"
            return f"{barra(avanco, meta)} {avanco}/{meta}"

        p1 = pagina(1, f"{EMOJI['genero']} {pendentes} sem gênero" if pendentes else "")
        if perfil["frase"]:
            frase = " ".join(perfil["frase"].split())  # itálico não atravessa quebras de linha
            p1.description = f"*{discord.utils.escape_markdown(frase)}*"
        campo(p1, f"{EMOJI['musica']} Música", ranks["pedidos"])
        campo(p1, f"{EMOJI['mensagens']} Mensagens", ranks["mensagens"])
        campo(p1, f"{EMOJI['mudae']} Mudae", ranks["mudae"])
        campo(p1, f"Nível {lvl}", nivel_texto(), False)
        campo(p1, f"{EMOJI['favorita']} Música favorita", perfil["favorita"], False)
        campo(p1, f"{EMOJI['mais_pedida']} Música mais colocada", mais, False)
        campo(p1, f"{EMOJI['genero']} Gênero favorito", genero, False)
        campo(
            p1,
            f"{EMOJI['atividade']} Atividade",
            f"{EMOJI['mensagens']} **{milhar(totais['mensagens'])}** mensagens · "
            f"{EMOJI['musica']} **{milhar(totais['pedidos'])}** pedidos · "
            f"{EMOJI['mudae']} **{milhar(totais['mudae'])}** roletadas",
            False,
        )
        campo(p1, f"{EMOJI['insignias']} Insígnias", insignias, False)

        p2 = pagina(2, "escolha com mm!titulo NOME")
        titulos = [CATALOGO[k]["nome"] for k, _ in self.banco.itens(uid, "titulo")]
        visiveis = []
        for texto in titulos:
            if len("\n".join(visiveis + [texto])) > 3400:
                break
            visiveis.append(texto)
        if not titulos:
            tem_insignia = bool(self.banco.itens(uid, "insignia"))
            p2.description = (
                "NÃO POSSUI TÍTULOS" if tem_insignia else "NÃO POSSUI TÍTULOS OU INSÍGNIAS"
            )
        else:
            p2.description = "\n".join(visiveis)
            if len(visiveis) < len(titulos):
                p2.description += (
                    f"\n\n+{len(titulos) - len(visiveis)} títulos\nUse mm!titulos para ver todos."
                )

        p3 = pagina(3)
        if perfil["capa"]:
            p3.set_image(url=perfil["capa"])
        campo(p3, f"Nível {lvl}", nivel_texto(), False)
        campo(p3, f"{EMOJI['mensagens']} Ranking de mensagens", ranks["mensagens"])
        campo(p3, f"{EMOJI['musica']} Ranking de música", ranks["pedidos"])
        campo(p3, f"{EMOJI['mudae']} Ranking do Mudae", ranks["mudae"])
        for fonte, rotulo, emoji in (
            ("mensagens", "Mensagens", EMOJI["mensagens"]),
            ("pedidos", "Músicas colocadas", EMOJI["musica"]),
        ):
            campo(
                p3,
                f"{emoji} {rotulo}",
                f"Total **{milhar(totais[fonte])}**\n"
                f"Ano **{milhar(self.banco.total_usuario(uid, fonte, ano))}**\n"
                f"Mês **{milhar(self.banco.total_usuario(uid, fonte, mes))}**",
            )
        campo(p3, f"{EMOJI['mais_pedida']} Música mais colocada", mais, False)
        # "One Hit Wonders" entra aqui quando o recurso existir (um campo vazio confunde).
        return [p1, p2, p3]

    # ----- mm!aleatoria ------------------------------------------------------
    @commands.command(name="aleatoria", aliases=["aleatória", "random"])
    @commands.guild_only()
    async def aleatoria(self, ctx: commands.Context):
        """Sorteia uma música do histórico e mostra o m!play pronto pra copiar."""
        sorteada = self.banco.musica_aleatoria()
        if not sorteada:
            await responder(
                ctx,
                "aviso",
                f"Ainda não tem música guardada (o dono precisa rodar `{PREFIX}read tudo`).",
            )
            return

        titulo, artista, vezes = sorteada
        busca = f"{titulo} {artista}".strip().replace("`", "'")
        comando = f"{COMANDO_PLAY} {busca}"[:300]

        async with ctx.typing():
            capa, genero = await self.deezer.info_seguro(
                f"{titulo} {artista}".strip(), titulo, artista
            )

        embed = discord.Embed(
            title="🎲 Música sorteada",
            description=(
                f"**{titulo}**"
                + (f"\n{artista}" if artista else "")
                + f"\n\nCopie e cole no chat pra tocar:\n```\n{comando}\n```"
            ),
            color=0x5865F2,
        )
        if capa:
            embed.set_thumbnail(url=capa)
        if genero:
            embed.add_field(name="🎼 Gênero", value=genero, inline=False)
        embed.set_footer(text=f"{BOT_NAME} • já tocou {vezes}x no chat")
        await ctx.send(embed=embed)

    # ----- mm!wrapped --------------------------------------------------------
    @commands.command(name="wrapped")
    @commands.guild_only()
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def wrapped(self, ctx: commands.Context):
        """Seu resumo dos últimos 12 meses de pedidos (mês atual + os 11 anteriores)."""
        autor = ctx.author
        agora = datetime.now(FUSO)
        ano, mes = agora.year, agora.month - 11
        if mes <= 0:
            mes += 12
            ano -= 1
        inicio = datetime(ano, mes, 1, tzinfo=FUSO)
        desde = discord.utils.time_snowflake(inicio)

        pedidos = self.banco.pedidos_do_usuario(autor.id, desde)
        if not pedidos:
            await responder(
                ctx,
                "aviso",
                f"**{autor.display_name}**, você não tem pedidos nos últimos 12 meses.",
            )
            return

        por_mes, por_dia_semana = Counter(), Counter()
        dias = set()
        consultas, exemplo = Counter(), {}
        sem_texto = 0
        for message_id, conteudo in pedidos:
            quando = data_local(message_id)
            por_mes[(quando.year, quando.month)] += 1
            por_dia_semana[quando.weekday()] += 1
            dias.add(quando.date())
            consulta = consulta_do_pedido(conteudo)
            if consulta is None:
                sem_texto += 1
            elif consulta:
                chave = normalizar(consulta)
                consultas[chave] += 1
                exemplo.setdefault(chave, " ".join(consulta.split()))

        # gráfico mês a mês (12 barras)
        maior = max(por_mes.values())
        linhas_grafico, contagens = [], []
        a, m = inicio.year, inicio.month
        for _ in range(12):
            n = por_mes[(a, m)]
            contagens.append(n)
            barra = "█" * max(1, round(10 * n / maior)) if n else "·"
            linhas_grafico.append(f"{MESES[m - 1]}/{str(a)[2:]} {barra:<10} {n}")
            m += 1
            if m > 12:
                m, a = 1, a + 1

        (a_top, m_top), n_top = por_mes.most_common(1)[0]
        dia_top = por_dia_semana.most_common(1)[0][0]

        ranking = self.banco.ranking_usuarios(desde, self.elegiveis())
        posicao = next((i for i, (uid, _) in enumerate(ranking, start=1) if uid == autor.id), None)

        # capa e gênero favorito: procura no Deezer os pedidos mais feitos
        top_consultas = consultas.most_common(WRAPPED_TOP_GENERO)
        capa, generos = "", Counter()
        top5 = top_consultas[:5]
        achados = [None] * len(top5)  # títulos dos links (YouTube/Spotify) do top 5
        if top_consultas:
            async with ctx.typing():
                infos, achados = await asyncio.gather(
                    asyncio.gather(
                        *(self.deezer.info_seguro(exemplo[ch]) for ch, _ in top_consultas)
                    ),
                    asyncio.gather(*(self.links.titulo_seguro(exemplo[ch]) for ch, _ in top5)),
                )
            for (_, q), (capa_i, genero_i) in zip(top_consultas, infos):
                if genero_i:
                    generos[genero_i] += q
                if capa_i and not capa:
                    capa = capa_i  # a do pedido mais feito que tiver capa

        descricao = f"De **{MESES[inicio.month - 1]}/{inicio.year}** até hoje."
        if sem_texto:
            descricao += (
                f"\n⚠️ {sem_texto} pedido(s) sem texto guardado — o dono precisa rodar "
                f"`{PREFIX}read tudo` pra completar o top de músicas."
            )
        embed = discord.Embed(
            title=f"🎁 Wrapped de {autor.display_name}", description=descricao, color=0xEB459E
        )
        embed.set_author(name=autor.display_name, icon_url=autor.display_avatar.with_size(128).url)
        if capa:
            embed.set_thumbnail(url=capa)
        embed.add_field(name="🎧 Pedidos", value=f"**{len(pedidos)}**", inline=True)
        embed.add_field(name="📅 Dias com pedido", value=f"**{len(dias)}**", inline=True)
        if posicao:
            embed.add_field(
                name="🏆 Posição", value=f"**#{posicao}** de {len(ranking)}", inline=True
            )
        embed.add_field(
            name="🔥 Mês mais ativo", value=f"{MESES[m_top - 1]}/{a_top} ({n_top})", inline=True
        )
        embed.add_field(name="🗓️ Dia favorito", value=DIAS_SEMANA[dia_top], inline=True)
        if generos:
            mais = generos.most_common(3)
            valor = f"**{mais[0][0]}** ({mais[0][1]})" + "".join(
                f"\n{g} ({n})" for g, n in mais[1:]
            )
            embed.add_field(name="🎼 Gênero favorito", value=valor, inline=True)
        if consultas:
            top = [
                f"**{i}.** {formatar_consulta(exemplo[ch], achado)} — {q}x"
                for i, ((ch, q), achado) in enumerate(zip(top5, achados), start=1)
            ]
            embed.add_field(name="🎵 Mais pedidos", value="\n".join(top), inline=False)
        canal_atual = getattr(ctx, "channel", None)
        arquivo = None
        if imagens.disponivel() and pode_anexar(canal_atual):
            arquivo = await self.gerar_imagem(
                imagens.gerar_wrapped,
                "wrapped.png",
                {
                    "nome": autor.display_name,
                    "nome_alt": getattr(autor, "name", ""),
                    "meses": contagens,
                    "pedidos": len(pedidos),
                    "dias": len(dias),
                    "genero": generos.most_common(1)[0][0] if generos else "",
                    "top": [
                        (achado[0] if achado else exemplo[ch], q)
                        for (ch, q), achado in zip(top5, achados)
                    ][:3],
                    "cor": self.banco.perfil(autor.id)["cor"],
                },
                await self.baixar_avatar(autor),
                canal=canal_atual,
            )
        embed.set_footer(text=BOT_NAME)
        if arquivo is None:  # sem imagem, o gráfico mês a mês vai em texto
            embed.add_field(
                name="📈 Mês a mês",
                value="```\n" + "\n".join(linhas_grafico) + "\n```",
                inline=False,
            )
            await ctx.send(embed=embed)
            return
        embed.set_image(url="attachment://wrapped.png")
        await ctx.send(embed=embed, file=arquivo)

    # ----- mm!exportar -------------------------------------------------------
    async def resolver_nomes(self, ids):
        """{usuario_id: nome}. Usa o cache e busca na API só até LIMITE_BUSCA_NOMES pessoas."""
        nomes, buscas = {}, 0
        for uid in ids:
            usuario = self.bot.get_user(uid)
            if usuario is None and buscas < LIMITE_BUSCA_NOMES:
                buscas += 1
                try:
                    usuario = await self.bot.fetch_user(uid)
                except discord.HTTPException:
                    usuario = None
            nomes[uid] = usuario.display_name if usuario else ""
        return nomes

    @commands.command(name="exportar", aliases=["export", "csv"], hidden=True)
    @commands.guild_only()
    @so_memi()
    @commands.cooldown(1, 30, commands.BucketType.user)
    async def exportar(self, ctx: commands.Context):
        """Manda tudo que o bot guardou em planilhas CSV."""
        async with ctx.typing():
            tocadas = self.banco.historico_tocadas()
            pedidos = self.banco.historico_pedidos()
            if not tocadas and not pedidos:
                await responder(
                    ctx,
                    "aviso",
                    f"Ainda não tem nada guardado pra exportar (o dono precisa rodar `{PREFIX}read tudo`).",
                )
                return

            musicas = self.banco.ranking_musicas()
            artistas = self.banco.ranking_artistas()
            usuarios = self.banco.ranking_usuarios()
            nomes = await self.resolver_nomes([uid for uid, _ in usuarios])

            def hora(message_id):
                return data_local(message_id).strftime("%d/%m/%Y %H:%M:%S")

            arquivos = [
                discord.File(
                    gerar_csv(
                        ("posicao", "titulo", "artista", "vezes_tocada"),
                        [(i, t, a, q) for i, (t, a, q) in enumerate(musicas, start=1)],
                    ),
                    filename="ranking_musicas.csv",
                ),
                discord.File(
                    gerar_csv(
                        ("posicao", "artista", "musicas_diferentes", "vezes_tocadas"),
                        [(i, a, n, q) for i, (a, n, q) in enumerate(artistas, start=1)],
                    ),
                    filename="ranking_artistas.csv",
                ),
                discord.File(
                    gerar_csv(
                        ("posicao", "usuario_id", "nome", "pedidos"),
                        [
                            (i, uid, nomes.get(uid, ""), q)
                            for i, (uid, q) in enumerate(usuarios, start=1)
                        ],
                    ),
                    filename="ranking_usuarios.csv",
                ),
                discord.File(
                    gerar_csv(
                        ("data_hora", "titulo", "artista", "bot", "canal_id"),
                        [(hora(mid), t, a, b or "", cid) for mid, t, a, b, cid in tocadas],
                    ),
                    filename="historico_tocadas.csv",
                ),
                discord.File(
                    gerar_csv(
                        ("data_hora", "usuario_id", "nome", "pedido", "canal_id"),
                        [
                            (hora(mid), uid, nomes.get(uid, ""), consulta_do_pedido(c) or "", cid)
                            for mid, uid, c, cid in pedidos
                        ],
                    ),
                    filename="historico_pedidos.csv",
                ),
            ]

        try:
            await ctx.send(
                f"📊 **Planilhas prontas!** {len(tocadas)} músicas tocadas • {len(pedidos)} pedidos.\n"
                "Separador `;` e horários em Brasília. O Excel abre direto; no Google Planilhas use "
                "Arquivo → Importar.",
                files=arquivos,
            )
        except discord.HTTPException:
            await responder(
                ctx,
                "erro",
                "Não consegui enviar: os arquivos passaram do limite de tamanho do Discord.",
            )


class PerfilView(discord.ui.View):
    def __init__(self, paginas):
        super().__init__(timeout=180)
        self.paginas, self.pagina, self.message = paginas, 0, None
        self.atualizar()

    def atualizar(self):
        self.anterior.disabled = self.pagina == 0
        self.proxima.disabled = self.pagina == len(self.paginas) - 1

    @discord.ui.button(label="◀", style=discord.ButtonStyle.secondary)
    async def anterior(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.pagina = max(0, self.pagina - 1)
        self.atualizar()
        await interaction.response.edit_message(embed=self.paginas[self.pagina], view=self)

    @discord.ui.button(label="▶", style=discord.ButtonStyle.secondary)
    async def proxima(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.pagina = min(len(self.paginas) - 1, self.pagina + 1)
        self.atualizar()
        await interaction.response.edit_message(embed=self.paginas[self.pagina], view=self)

    async def on_timeout(self):
        for button in self.children:
            button.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


class Atividade(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.musicas = bot.get_cog("Musicas")
        self.banco = self.musicas.banco

    async def cog_check(self, ctx):
        return await self.musicas.cog_check(ctx)

    @commands.command(name="scan", hidden=True)
    @commands.guild_only()
    @so_memi()
    async def scan(self, ctx):
        await self.musicas.iniciar_leitura(
            ctx,
            "🔎 Importando o histórico de todos os chats e threads, sem avisos...",
            completo=True,
        )

    async def mostrar_ranking(self, ctx, titulo, linhas, unidade, levels=False, subtitulo=""):
        itens = [
            f"**{self.musicas.nome_pessoa(uid)}** · "
            + (f"nível {nivel(n)} · " if levels else "")
            + f"{milhar(n)} {unidade}"
            for uid, n in linhas
        ]
        await self.musicas.enviar_ranking(
            ctx,
            titulo,
            itens,
            rodape_partes=(
                f"{len(linhas)} participantes",
                f"{milhar(sum(n for _, n in linhas))} {unidade}",
            ),
            subtitulo=subtitulo,
            meu_indice=indice_do_usuario(linhas, ctx.author.id),
        )

    @commands.command(name="tagarelas")
    @commands.guild_only()
    async def tagarelas(self, ctx, *args):
        filtros = {
            "geral": None,
            "todos": None,
            "ios": False,
            "pessoas": False,
            "bot": True,
            "bots": True,
        }
        filtros.update({sem_acento(nome): False for nome in NOMES_RANKING_USUARIO})
        periodo = ""
        tipos = []
        for arg in args:
            arg = sem_acento(arg)
            if arg in ("mes", "ano") and not periodo:
                periodo = arg
            elif arg in filtros:
                tipos.append(filtros[arg])
            else:
                await responder(ctx, "aviso", "Use mm!tagarelas [ios|bot] [mes|ano].")
                return
        if len(set(tipos)) > 1:
            await responder(ctx, "aviso", "Escolha ios, bot ou geral.")
            return
        filtro = tipos[0] if tipos else None
        desde, rotulo = intervalo(periodo)
        linhas = self.musicas.ranking("mensagens", desde, filtro)
        await self.mostrar_ranking(
            ctx, "💬 Quem mais mandou mensagem", linhas, "mensagens", subtitulo=rotulo
        )

    @commands.command(name="hall", aliases=["halldafama"])
    @commands.guild_only()
    async def hall(self, ctx, *args):
        palavras = [sem_acento(x) for x in args]
        if palavras not in ([], ["mes"], ["meses"], ["ano"], ["anos"]):
            await responder(ctx, "aviso", "Use mm!hall [mes|ano].")
            return
        tipo = "ano" if palavras and palavras[0].startswith("ano") else "mes"

        def quem(uid):
            return self.musicas.nome_pessoa(uid) if uid is not None else "—"

        itens = [
            f"**{rotulo_periodo(tipo, periodo)}** · {EMOJI['musica']} {quem(dj)}"
            f" · {EMOJI['mensagens']} {quem(tagarela)}"
            for periodo, dj, tagarela in self.banco.hall(tipo)
        ]
        await self.musicas.enviar_ranking(
            ctx,
            "🏛️ Hall da fama",
            itens,
            subtitulo="anos" if tipo == "ano" else "meses",
            rodape_partes=(f"{len(itens)} períodos",),
            numerar=False,
        )

    @commands.command(name="levels")
    @commands.guild_only()
    async def levels(self, ctx, *args):
        if args:
            await responder(ctx, "aviso", "mm!levels usa o total histórico, sem flags.")
            return
        await self.mostrar_ranking(
            ctx,
            "🏆 Ranking de níveis",
            self.musicas.ranking("mensagens", eh_bot=False),
            "mensagens",
            True,
        )

    @commands.command(name="mudae")
    @commands.guild_only()
    async def mudae(self, ctx, *args):
        if args:
            await responder(ctx, "aviso", "mm!mudae usa o total histórico, sem flags.")
            return
        await self.mostrar_ranking(
            ctx, "🎎 Ranking de roletadas", self.musicas.ranking("mudae", eh_bot=False), "roletadas"
        )

    @commands.command(name="give", hidden=True)
    @commands.guild_only()
    @so_memi()
    async def give(self, ctx, tipo: str, pessoa: discord.Member, *, nome: str):
        try:
            novo = self.banco.conceder_manual(pessoa.id, tipo, nome, pessoa.bot)
        except ValueError as erro:
            await responder(ctx, "erro", str(erro)[:1900])
            return
        if novo:
            await responder(ctx, "sucesso", "Item concedido.")
        else:
            await responder(ctx, "aviso", "Essa pessoa já possui esse item.")

    async def mostrar_itens(self, ctx, pessoa, tipo):
        pessoa = pessoa or ctx.author
        rotulo = "Insígnias" if tipo == "insignia" else "Títulos"
        itens = []
        for item, n in self.banco.itens(pessoa.id, tipo):
            info = CATALOGO[item]
            nome = info.get("nome_insignia", info["nome"]) if tipo == "insignia" else info["nome"]
            itens.append(
                (f"{info['emoji']} " + (f"x{n} " if n > 1 else "") if tipo == "insignia" else "")
                + nome
            )
        titulo = f"{rotulo} de {pessoa.display_name}"
        if not itens:
            aviso = discord.Embed(
                title=titulo, description=f"NÃO POSSUI {rotulo.upper()}", color=COR_PADRAO
            )
            await ctx.send(embed=aviso, allowed_mentions=discord.AllowedMentions.none())
            return
        contagem = f"{len(itens)} {'item' if len(itens) == 1 else 'itens'}"
        view = RankingView(titulo, itens, (contagem,), numerar=False)
        view.message = await ctx.send(
            embed=view.montar_embed(), view=view, allowed_mentions=discord.AllowedMentions.none()
        )

    @commands.command(name="insignias", aliases=["insígnias"])
    @commands.guild_only()
    async def insignias(self, ctx, pessoa: discord.Member = None):
        await self.mostrar_itens(ctx, pessoa, "insignia")

    @commands.command(name="titulos", aliases=["títulos"])
    @commands.guild_only()
    async def titulos(self, ctx, pessoa: discord.Member = None):
        await self.mostrar_itens(ctx, pessoa, "titulo")

    @commands.command(name="cartao", aliases=["cartão", "card"])
    @commands.guild_only()
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def cartao(self, ctx, pessoa: discord.Member = None):
        """Cartão de perfil em imagem (precisa do Pillow; sem ele mostra o perfil comum)."""
        pessoa = pessoa or ctx.author
        if not imagens.disponivel():
            await responder(
                ctx,
                "aviso",
                "O cartão em imagem precisa do Pillow "
                "(`python -m pip install -r requirements.txt`). Mostrando o perfil comum.",
            )
            await Musicas.perfil.callback(self.musicas, ctx, pessoa)
            return
        if not pode_anexar(getattr(ctx, "channel", None)):
            await responder(
                ctx,
                "aviso",
                "Preciso da permissão de anexar arquivos neste canal para mostrar o cartão. "
                "Mostrando o perfil comum.",
            )
            await Musicas.perfil.callback(self.musicas, ctx, pessoa)
            return
        async with ctx.typing():
            avatar = await self.musicas.baixar_avatar(pessoa)
            dados = self.musicas.dados_cartao(pessoa)
            arquivo = await self.musicas.gerar_imagem(
                imagens.gerar_cartao, "cartao.png", dados, avatar
            )
        if arquivo is None:
            await responder(ctx, "erro", "Não consegui gerar o cartão agora. Tente de novo.")
            return
        cor = COR_PADRAO if dados["cor"] is None else dados["cor"]
        embed = discord.Embed(color=cor)
        embed.set_image(url="attachment://cartao.png")
        await ctx.send(embed=embed, file=arquivo, allowed_mentions=discord.AllowedMentions.none())

    @commands.command(name="frase")
    @commands.guild_only()
    async def frase(self, ctx, *, texto: str = ""):
        if len(texto) > 100:
            await responder(ctx, "aviso", "A frase pode ter no máximo 100 caracteres.")
            return
        self.banco.salvar_perfil(ctx.author.id, frase=texto)
        await responder(ctx, "sucesso", "Frase salva.")

    @commands.command(name="favorita")
    @commands.guild_only()
    async def favorita(self, ctx, *, texto: str):
        texto = texto.strip()
        if not texto or len(texto) > 200:
            await responder(
                ctx, "aviso", "Use o nome da música e do artista, com até 200 caracteres."
            )
            return
        titulo, artista = dividir(texto, RE_TRACO)
        async with ctx.typing():
            capa, _ = await self.musicas.deezer.info_seguro(texto, titulo, artista)
        self.banco.salvar_perfil(ctx.author.id, favorita=texto, capa=capa)
        await responder(
            ctx,
            "sucesso",
            (
                "Música favorita salva. Capa encontrada no Deezer."
                if capa
                else "Música favorita salva. Não encontrei a capa no Deezer agora."
            ),
        )

    @commands.command(name="ec")
    @commands.guild_only()
    async def ec(self, ctx, *, texto: str = ""):
        """Troca a cor do embed do seu perfil (mm!ec COR)."""
        uso = (
            f"Use `{PREFIX}ec COR`, com um código hex (`#ff8800`) ou um nome ("
            + ", ".join(CORES)
            + f"). `{PREFIX}ec padrao` volta à cor original."
        )
        if not texto.strip():
            await responder(ctx, "aviso", uso)
            return
        try:
            cor = interpretar_cor(texto)
        except ValueError:
            await responder(ctx, "erro", "Cor inválida. " + uso)
            return
        self.banco.salvar_perfil(ctx.author.id, cor=cor)
        await responder(
            ctx,
            "sucesso",
            (
                "Cor do perfil restaurada."
                if cor is None
                else f"Cor do perfil atualizada para `#{cor:06X}`."
            ),
        )

    @commands.command(name="titulo", aliases=["título"])
    @commands.guild_only()
    async def titulo(self, ctx, *, nome: str):
        if self.banco.selecionar_titulo(ctx.author.id, nome):
            await responder(ctx, "sucesso", "Título favorito atualizado.")
        else:
            await responder(
                ctx, "aviso", "Você não possui esse título. Veja os seus com mm!titulos."
            )


AJUDA = [
    (
        "🏆 Rankings",
        [
            ("mm!tagarelas", "mensagens · flags: ios, bot, mes, ano"),
            ("mm!levels", "níveis"),
            ("mm!mudae", "roletadas do Mudae"),
            ("mm!hall", "vencedores dos meses e anos anteriores · flag: ano"),
        ],
    ),
    (
        "🎵 Música",
        [
            ("mm!musicas", "flags: artista, ios, genero [NOME], mes, ano"),
            ("mm!aleatoria", "sorteia uma música"),
            ("mm!wrapped", "seu resumo dos últimos 12 meses"),
        ],
    ),
    (
        "👤 Perfil e conquistas",
        [
            ("mm!perfil [@pessoa]", "perfil em 3 páginas"),
            ("mm!insignias [@pessoa]", "insígnias"),
            ("mm!titulos [@pessoa]", "títulos"),
            ("mm!titulo NOME", "escolhe um título que você possui"),
            ("mm!frase TEXTO", "frase de até 100 caracteres"),
            ("mm!favorita MÚSICA - ARTISTA", "música favorita"),
            ("mm!cartao [@pessoa]", "seu cartão de perfil em imagem"),
            ("mm!ec COR", "cor do seu perfil (#hex ou nome; padrao restaura)"),
        ],
    ),
    (
        "ℹ️ Sobre o bot",
        [("mm!changelog", "o que mudou em cada versão")],
    ),
]


class ChangelogView(discord.ui.View):
    """Uma versão do histórico com um menu para trocar de versão (também as passadas)."""

    def __init__(self, indice=0):
        super().__init__(timeout=180)
        self.indice = indice
        self.message = None
        self.select = discord.ui.Select(placeholder="Ver outra versão", options=self._opcoes())
        self.select.callback = self._escolheu
        self.add_item(self.select)

    def _opcoes(self):
        return [
            discord.SelectOption(
                label=f"v{entrada['versao']}",
                description=" · ".join(x for x in (entrada["data"], entrada["titulo"]) if x)[:100],
                value=str(i),
                default=i == self.indice,
            )
            for i, entrada in enumerate(versoes_bot.VERSOES[:25])
        ]

    def montar_embed(self):
        return embed_changelog(
            versoes_bot.VERSOES[self.indice], self.indice, len(versoes_bot.VERSOES)
        )

    async def trocar(self, interaction, indice):
        self.indice = indice
        for i, opcao in enumerate(self.select.options):
            opcao.default = i == indice
        await interaction.response.edit_message(embed=self.montar_embed(), view=self)

    async def _escolheu(self, interaction):
        await self.trocar(interaction, int(self.select.values[0]))

    async def on_timeout(self):
        self.select.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


class Ajuda(commands.Cog):
    @commands.command(name="help", aliases=["ajuda", "comandos"])
    async def ajuda(self, ctx):
        embed = embed_ajuda(AJUDA)
        musicas = self.bot.get_cog("Musicas") if self.bot else None
        arquivo = None
        if musicas:
            arquivo = await musicas.gerar_imagem(
                imagens.gerar_ajuda, "ajuda.png", canal=getattr(ctx, "channel", None)
            )
        if arquivo is None:
            await ctx.send(embed=embed)
            return
        embed.set_image(url="attachment://ajuda.png")
        await ctx.send(embed=embed, file=arquivo)

    @commands.command(name="changelog", aliases=["novidades", "versao", "versão"])
    async def changelog(self, ctx, versao: str = ""):
        """Mudanças da versão mais recente; `mm!changelog 2.0.0` ou o menu mostram as anteriores."""
        indice = versoes_bot.buscar(versao) if versao else 0
        if indice is None:
            disponiveis = ", ".join(f"`{v['versao']}`" for v in versoes_bot.VERSOES)
            await responder(
                ctx, "aviso", f"Não achei a versão `{versao[:20]}`. Disponíveis: {disponiveis}."
            )
            return
        view = ChangelogView(indice)
        view.message = await ctx.send(
            embed=view.montar_embed(), view=view, allowed_mentions=discord.AllowedMentions.none()
        )

    def __init__(self, bot):
        self.bot = bot


# Adicione novas funcionalidades aqui (cada uma como um Cog separado)
COGS = [Musicas, Atividade, Ajuda]  # Atividade usa o banco do Musicas: vem depois dele


class MeMiBot(commands.Bot):
    async def setup_hook(self):
        for cog in COGS:
            await self.add_cog(cog(self))

    async def on_ready(self):
        await self.change_presence(activity=discord.Game(name=f"{PREFIX}help"))
        logging.info("%s v%s online como %s", BOT_NAME, versoes_bot.VERSAO_ATUAL, self.user)

    async def on_command_error(self, ctx, error):
        if isinstance(error, commands.CommandNotFound):
            return
        if isinstance(error, commands.NoPrivateMessage):
            await responder(ctx, "aviso", "Esse comando só funciona dentro de um servidor.")
            return
        if isinstance(error, commands.CommandOnCooldown):
            await responder(ctx, "aviso", f"Calma! Tente de novo em {error.retry_after:.0f}s.")
            return
        if isinstance(error, commands.CheckFailure):
            await responder(ctx, "erro", str(error))
            return
        if isinstance(error, commands.MissingRequiredArgument):
            await responder(
                ctx,
                "aviso",
                f"Faltou um argumento. Use `mm!{ctx.command.qualified_name} {ctx.command.signature}`",
            )
            return
        if isinstance(error, commands.BadArgument):
            await responder(ctx, "aviso", f"Não entendi esse argumento. Veja `{PREFIX}help`.")
            return
        logging.error(
            "Erro no comando %s", ctx.command, exc_info=(type(error), error, error.__traceback__)
        )
        await responder(
            ctx, "erro", "Não consegui concluir o comando. O erro ficou salvo no memi_bot.log."
        )


def criar_intents(membros):
    intents = discord.Intents.default()
    intents.members = membros
    intents.message_content = True  # ative também no Developer Portal!
    return intents


def prefixo(bot, message):
    """Aceita o prefixo em qualquer combinação de maiúsculas/minúsculas (MM!, Mm!, mm!...)."""
    prefixos = commands.when_mentioned(bot, message)
    inicio = (message.content or "")[: len(PREFIX)]
    if inicio.lower() == PREFIX.lower():
        prefixos.append(inicio)  # devolve exatamente o que a pessoa digitou
    return prefixos


def criar_bot(membros=MEMBERS_INTENT):
    # case_insensitive: também aceita o nome do comando em maiúsculas (mm!RANKING, mm!Perfil...)
    return MeMiBot(
        command_prefix=prefixo,
        case_insensitive=True,
        intents=criar_intents(membros),
        help_command=None,
        allowed_mentions=discord.AllowedMentions.none(),
    )


bot = criar_bot(MEMBERS_INTENT)


def executar():
    """Roda o bot até o fim. Ctrl+C encerra sem traceback."""
    try:
        asyncio.run(conectar())
    except KeyboardInterrupt:
        pass
    except discord.PrivilegedIntentsRequired:
        logging.error(MSG_INTENT_MENSAGENS)
        raise SystemExit(MSG_INTENT_MENSAGENS)
    except Exception:
        logging.exception("O bot não pôde continuar.")
        raise


async def conectar():
    """Conecta o bot. Se o Discord recusar a Server Members Intent (não ativada no portal), segue
    sem ela em vez de falhar: a atualização não deve exigir nenhum passo manual."""
    global bot
    try:
        async with bot:
            await bot.start(TOKEN)
    except discord.PrivilegedIntentsRequired:
        if not bot.intents.members:
            raise  # a recusada foi outra intent; não há alternativa
        logging.warning(MSG_INTENT_MEMBROS)
        bot = criar_bot(False)
        async with bot:
            await bot.start(TOKEN)


if __name__ == "__main__":
    configurar_log()
    if not TOKEN or TOKEN == "COLE_SEU_TOKEN_AQUI":
        logging.error("Falta configurar token.txt na mesma pasta do bot.")
        raise SystemExit(
            'Falta o token! Abra o arquivo "token.txt" (na mesma pasta do bot) '
            "e troque COLE_SEU_TOKEN_AQUI pelo seu token (só o token, sem aspas)."
        )
    trava = travar_instancia()  # a referência precisa continuar viva enquanto o bot rodar
    if trava is None:
        logging.error("Outra cópia do MeMi BOT já está rodando; esta foi encerrada.")
        raise SystemExit("O MeMi BOT já está rodando. Feche a outra cópia antes de abrir esta.")
    executar()
