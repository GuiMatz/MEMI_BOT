"""
MeMi BOT
Funcionalidade 1: rankings de música do chat.

  - Músicas e artistas: vêm dos avisos "tocando agora" do Pancake e do Jockie Music.
  - Usuários: vêm das mensagens de pessoas escrevendo m!play / p!play (pedidos).

Leitura automática (sem precisar de comando):
  - Com o bot ligado, cada m!play/p!play e cada aviso de "tocando agora" nos chats configurados
    é guardado na hora.
  - Ao ligar (ou reconectar), o bot lê tudo que passou desde a última mensagem que ele guardou
    e que ele não viu porque estava desligado.

Comandos:
  mm!read                    -> (só o dono) lê os chats configurados e guarda tudo no banco
  mm!read #canal1 #canal2    -> (só o dono) lê só esses canais
  mm!read tudo               -> (só o dono) relê tudo do zero (seguro: não duplica)
  mm!musicas                 -> ranking geral das músicas mais tocadas
  mm!musicas artista         -> ranking de artistas (por nº de músicas diferentes)
  mm!musicas ios             -> ranking de quem mais pediu música
  mm!musicas [tipo] semana|mes|ano -> qualquer ranking filtrado por período (capa do álbum no topo)
  mm!perfil [@pessoa]        -> perfil: pedidos, mensagens (do mm!scan), ranking de cada um e música mais pedida
  mm!aleatoria               -> sorteia uma música (com capa e gênero) e mostra o m!play pra copiar e colar
  mm!wrapped                 -> resumo dos seus últimos 12 meses de pedidos (com capa e gênero favorito)
  mm!exportar                -> manda o banco inteiro em planilhas CSV
  mm!tagarelas [ios|bot]     -> quem mais mandou mensagem (geral / só pessoas / só bots)
  mm!scan                    -> (só dono, escondido) conta as mensagens de TODOS os chats
  mm!help                    -> lista de comandos

IMPORTANTE: o texto dos pedidos (usado no perfil e no wrapped) só passou a ser guardado
nesta versão. Rode "mm!read tudo" UMA vez pra preencher os pedidos antigos.

Capas e gêneros vêm da API pública do Deezer (sem chave) e ficam guardados no banco
(tabela deezer_cache), então cada música/pedido só é consultado uma vez.
"""
import asyncio
import csv
import io
import os
import re
import sqlite3
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import aiohttp
import discord
from discord.ext import commands

# --- CONFIGURAÇÃO ---------------------------------------------------------
# O token fica no arquivo "token.txt" na mesma pasta do bot (só o token, nada mais).
_ARQ_TOKEN = Path(__file__).with_name("token.txt")
TOKEN = _ARQ_TOKEN.read_text(encoding="utf-8").strip() if _ARQ_TOKEN.exists() else ""

BOT_NAME = "MeMi BOT"
PREFIX = "mm!"

# IDs dos chats que o "mm!read" deve ler. Deixe vazio para ler todos os chats acessíveis.
CANAIS_MUSICA = [
    int(valor.strip())
    for valor in os.getenv("MEMI_MUSIC_CHANNEL_IDS", "").split(",")
    if valor.strip()
]

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

# mm!tagarelas <nome>: sem nome = geral | ios = só pessoas | bot = só bots
NOMES_TAGARELAS_GERAL = ("", "geral", "todos")
NOMES_TAGARELAS_BOT = ("bot", "bots")

# Períodos do ranking: mm!musicas [tipo] semana|mes|ano  (contando de agora pra trás)
# nome digitado -> (rótulo mostrado, dias)
PERIODOS = {
    "semana": ("últimos 7 dias", 7),
    "mes": ("últimos 30 dias", 30),
    "mês": ("últimos 30 dias", 30),
    "ano": ("último ano", 365),
}

# Fuso usado no Wrapped e nas planilhas (Brasília). O ID do Discord guarda o horário em UTC.
FUSO = timezone(timedelta(hours=-3))
MESES = ("jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez")
DIAS_SEMANA = ("segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo")

# mm!aleatoria mostra este comando (num embed) pra pessoa copiar e colar no chat
COMANDO_PLAY = "m!play"
DEEZER_API = "https://api.deezer.com"   # API pública, não precisa de chave
DEEZER_ESPERA = 8                       # segundos máx. esperando o Deezer por comando
DEEZER_REPETIR_APOS = 7 * 24 * 3600     # quando não achou capa/gênero, tenta de novo depois de 7 dias
YOUTUBE_OEMBED = "https://www.youtube.com/oembed"   # títulos de links: públicos, sem chave
SPOTIFY_OEMBED = "https://open.spotify.com/oembed"
LINK_ESPERA = 6                         # segundos máx. esperando o título de um link
LINK_REPETIR_APOS = 24 * 3600           # link cujo título não veio: tenta de novo depois de 1 dia
WRAPPED_TOP_GENERO = 10                 # quantos pedidos mais feitos entram na conta do gênero favorito
LIMITE_BUSCA_NOMES = 150    # máx. de pessoas buscadas na API ao exportar (o resto sai só com o ID)

POR_PAGINA = 10                                      # itens por página do ranking
ARQUIVO_BANCO = Path(__file__).with_name("memi.db")  # fica na mesma pasta do bot
LOTE = 200                                           # quantos registros salvar por vez
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


RE_TRACO = re.compile(r"\s+[-–—]\s+")   # Pancake: "Título - Artista"
RE_BY = re.compile(r"\s+by\s+", re.I)   # Jockie:  "Título by Artista"
RE_JOCKIE = re.compile(r"started playing\s*(.+)", re.I | re.S)


def dividir(nome: str, separador):
    """Divide no ÚLTIMO separador (assim 'Stand by Me by Ben E. King' funciona)."""
    achados = list(separador.finditer(nome))
    if not achados:
        return nome, ""
    m = achados[-1]
    titulo, artista = nome[:m.start()].strip(), nome[m.end():].strip()
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


def data_local(message_id: int) -> datetime:
    """Horário (fuso de Brasília) em que a mensagem foi enviada, tirado do próprio ID."""
    return discord.utils.snowflake_time(message_id).astimezone(FUSO)


# ===== Banco de dados ======================================================
class Banco:
    """
    musicas -> UMA linha por música (título + artista únicos). Nunca repete.
    tocadas -> UMA linha por vez que tocou (ID da mensagem do Discord é a chave: não duplica).
    pedidos -> UMA linha por mensagem de pessoa pedindo música (ID da mensagem: não duplica).
    """

    def __init__(self, caminho):
        self.con = sqlite3.connect(caminho)

        # banco de versão muito antiga (sem a tabela de músicas): descarta e refaz com mm!read
        colunas = [r[1] for r in self.con.execute("PRAGMA table_info(tocadas)")]
        if colunas and "musica_id" not in colunas:
            self.con.executescript("DROP TABLE tocadas; DROP TABLE IF EXISTS canais_lidos;")

        self.con.executescript(
            """
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
            """
        )

        # banco criado por versão anterior: ganha a coluna do artista normalizado
        if "artista_chave" not in [r[1] for r in self.con.execute("PRAGMA table_info(musicas)")]:
            self.con.execute("ALTER TABLE musicas ADD COLUMN artista_chave TEXT NOT NULL DEFAULT ''")
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
            musica_id = cur.execute(
                "SELECT id FROM musicas WHERE chave = ?", (chave,)
            ).fetchone()[0]
            cur.execute(
                "INSERT OR IGNORE INTO tocadas (message_id, channel_id, musica_id, bot) "
                "VALUES (?, ?, ?, ?)",
                (message_id, channel_id, musica_id, bot),
            )
        self.con.commit()

    def inserir_pedidos(self, linhas):
        """linhas: (message_id, channel_id, usuario_id, conteudo).
        Mensagem repetida não duplica; só completa o texto (pedidos antigos ganham o conteúdo)."""
        self.con.executemany(
            "INSERT INTO pedidos (message_id, channel_id, usuario_id, conteudo) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(message_id) DO UPDATE SET conteudo = excluded.conteudo",
            linhas,
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

    def ranking_musicas(self, desde=0):
        """[(titulo, artista, vezes_tocada), ...] da mais tocada pra menos tocada.
        desde: só conta o que veio de um ID de mensagem em diante (o ID carrega a data)."""
        return self.con.execute(
            "SELECT m.titulo, m.artista, COUNT(*) AS c "
            "FROM tocadas t JOIN musicas m ON m.id = t.musica_id "
            "WHERE t.message_id >= ? "
            "GROUP BY m.id ORDER BY c DESC, m.titulo COLLATE NOCASE",
            (desde,),
        ).fetchall()

    def ranking_artistas(self, desde=0):
        """[(artista, musicas_diferentes, vezes_tocadas), ...] por nº de músicas ÚNICAS."""
        return self.con.execute(
            "SELECT MIN(m.artista) AS artista, "
            "       COUNT(DISTINCT m.id) AS musicas, "
            "       COUNT(t.message_id) AS tocadas "
            "FROM musicas m JOIN tocadas t ON t.musica_id = m.id "
            "WHERE m.artista_chave <> '' AND t.message_id >= ? "
            "GROUP BY m.artista_chave "
            "ORDER BY musicas DESC, tocadas DESC, artista COLLATE NOCASE",
            (desde,),
        ).fetchall()

    def ranking_usuarios(self, desde=0):
        """[(usuario_id, pedidos), ...] de quem mais pediu pra quem menos pediu."""
        return self.con.execute(
            "SELECT usuario_id, COUNT(*) AS c FROM pedidos WHERE message_id >= ? "
            "GROUP BY usuario_id ORDER BY c DESC, usuario_id",
            (desde,),
        ).fetchall()

    def pedido_extremo(self, usuario_id, primeiro: bool):
        """(message_id, channel_id, conteudo) do primeiro ou do último pedido da pessoa."""
        ordem = "ASC" if primeiro else "DESC"
        return self.con.execute(
            "SELECT message_id, channel_id, conteudo FROM pedidos "
            f"WHERE usuario_id = ? ORDER BY message_id {ordem} LIMIT 1",
            (usuario_id,),
        ).fetchone()

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

    def substituir_atividade(self, contagem, eh_bot, quando: int):
        """Troca a contagem inteira de mensagens (resultado do mm!scan). Tudo ou nada."""
        with self.con:
            self.con.execute("DELETE FROM atividade")
            self.con.executemany(
                "INSERT INTO atividade (autor_id, eh_bot, mensagens) VALUES (?, ?, ?)",
                [(uid, int(eh_bot[uid]), n) for uid, n in contagem.items()],
            )
            self.con.execute(
                "INSERT INTO atividade_scan (id, quando) VALUES (1, ?) "
                "ON CONFLICT(id) DO UPDATE SET quando = excluded.quando",
                (quando,),
            )

    def ranking_atividade(self, eh_bot=None):
        """[(autor_id, mensagens), ...]. eh_bot: None = todos, False = pessoas, True = bots."""
        if eh_bot is None:
            return self.con.execute(
                "SELECT autor_id, mensagens FROM atividade ORDER BY mensagens DESC, autor_id"
            ).fetchall()
        return self.con.execute(
            "SELECT autor_id, mensagens FROM atividade WHERE eh_bot = ? ORDER BY mensagens DESC, autor_id",
            (int(eh_bot),),
        ).fetchall()

    def atividade_da_pessoa(self, autor_id):
        """(mensagens, posicao, total_do_grupo) da pessoa/bot no último mm!scan, ou None.
        A posição compara pessoas com pessoas e bots com bots (mesmo desempate do ranking)."""
        linha = self.con.execute(
            "SELECT mensagens, eh_bot FROM atividade WHERE autor_id = ?", (autor_id,)
        ).fetchone()
        if not linha:
            return None
        mensagens, eh_bot = linha
        acima = self.con.execute(
            "SELECT COUNT(*) FROM atividade WHERE eh_bot = ? "
            "AND (mensagens > ? OR (mensagens = ? AND autor_id < ?))",
            (eh_bot, mensagens, mensagens, autor_id),
        ).fetchone()[0]
        total = self.con.execute("SELECT COUNT(*) FROM atividade WHERE eh_bot = ?", (eh_bot,)).fetchone()[0]
        return mensagens, acima + 1, total

    def quando_scan(self):
        """Momento (unix) do último mm!scan, ou None."""
        linha = self.con.execute("SELECT quando FROM atividade_scan WHERE id = 1").fetchone()
        return linha[0] if linha else None

    def historico_pedidos(self):
        """[(message_id, usuario_id, conteudo, channel_id), ...] em ordem cronológica."""
        return self.con.execute(
            "SELECT message_id, usuario_id, conteudo, channel_id FROM pedidos ORDER BY message_id"
        ).fetchall()


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

    async def _get(self, caminho, params=None):
        """JSON da resposta, ou None se deu erro (rede, limite, id inexistente...)."""
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=6),
                headers={"User-Agent": f"{BOT_NAME}/1.0"},
            )
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
        if guardado and ((guardado[0] and guardado[1]) or agora - guardado[2] < DEEZER_REPETIR_APOS):
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
    "track": "música", "album": "álbum", "playlist": "playlist",
    "artist": "artista", "episode": "episódio", "show": "podcast",
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
            return f"https://open.spotify.com/{partes[0]}/{partes[1]}", SPOTIFY_OEMBED, TIPOS_SPOTIFY[partes[0]]
    return None


def milhar(n: int) -> str:
    """1234 -> '1.234' (jeito brasileiro)."""
    return f"{n:,}".replace(",", ".")


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
            nome = nome[:limite - 1] + "…"
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
class RankingView(discord.ui.View):
    """Ranking paginado com botões ◀ ▶ (qualquer pessoa pode trocar de página). `itens` são textos prontos."""

    def __init__(self, autor_id, titulo, itens, rodape, capa=""):
        super().__init__(timeout=180)
        self.autor_id = autor_id
        self.titulo = titulo
        self.itens = itens
        self.rodape = rodape
        self.capa = capa
        self.pagina = 0
        self.total_paginas = max(1, -(-len(itens) // POR_PAGINA))
        self.message = None
        self._atualizar_botoes()

    def montar_embed(self) -> discord.Embed:
        ini = self.pagina * POR_PAGINA
        fatia = self.itens[ini:ini + POR_PAGINA]
        medalhas = {1: "🥇 ", 2: "🥈 ", 3: "🥉 "}
        linhas = [
            f"{medalhas.get(ini + i, '')}**{ini + i}** - {item}"
            for i, item in enumerate(fatia, start=1)
        ]
        embed = discord.Embed(title=self.titulo, description="\n".join(linhas), color=0x5865F2)
        if self.capa:
            embed.set_thumbnail(url=self.capa)  # capa pequena no topo, em todas as páginas
        embed.set_footer(
            text=f"{BOT_NAME} • página {self.pagina + 1}/{self.total_paginas} • {self.rodape}"
        )
        return embed

    def _atualizar_botoes(self):
        self.anterior.disabled = self.pagina == 0
        self.proxima.disabled = self.pagina >= self.total_paginas - 1

    @discord.ui.button(label="◀", style=discord.ButtonStyle.secondary)
    async def anterior(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.pagina -= 1
        self._atualizar_botoes()
        await interaction.response.edit_message(embed=self.montar_embed(), view=self)

    @discord.ui.button(label="▶", style=discord.ButtonStyle.secondary)
    async def proxima(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.pagina += 1
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
        self.deezer = Deezer(self.banco)
        self.links = Links(self.banco)
        self.lock = asyncio.Lock()  # impede duas leituras ao mesmo tempo
        self.sincronizados = set()  # canais que já foram "atualizados" desde que o bot ligou

    async def cog_unload(self):
        await self.deezer.fechar()
        await self.links.fechar()

    # ----- leitura automática ------------------------------------------------
    def canais_para_sincronizar(self):
        """Os chats que o bot acompanha (os configurados, ou todos que ele enxerga)."""
        if CANAIS_MUSICA:
            achados = (self.bot.get_channel(cid) for cid in CANAIS_MUSICA)
            return [c for c in achados if c is not None and hasattr(c, "history")]
        canais = []
        for guild in self.bot.guilds:
            eu = guild.me
            canais += [
                c for c in list(guild.text_channels) + list(guild.voice_channels)
                if c.permissions_for(eu).view_channel and c.permissions_for(eu).read_message_history
            ]
        return canais

    async def sincronizar(self):
        """Lê o que chegou enquanto o bot estava fora (desde a última mensagem guardada de cada chat)."""
        async with self.lock:
            antes = (self.banco.total(), self.banco.total_pedidos())
            for canal in self.canais_para_sincronizar():
                try:
                    await self.ler_canal(canal, completo=False)
                    # só agora as mensagens ao vivo podem avançar a marca deste chat
                    self.sincronizados.add(canal.id)
                except discord.Forbidden:
                    print(f"[sincronizar] sem acesso a {canal.id}")
                except Exception:  # noqa: BLE001 - uma falha não pode derrubar o bot
                    import traceback
                    traceback.print_exc()
            depois = (self.banco.total(), self.banco.total_pedidos())
            print(f"[sincronizar] {depois[0] - antes[0]} músicas novas • {depois[1] - antes[1]} pedidos novos")

    @commands.Cog.listener()
    async def on_ready(self):
        # a cada (re)conexão completa: recomeça a sincronização pra não deixar buraco no histórico
        self.sincronizados.clear()
        await self.sincronizar()

    @commands.Cog.listener()
    async def on_message(self, msg: discord.Message):
        """Guarda na hora cada pedido (m!play/p!play) e cada aviso de música tocando."""
        if msg.guild is None:
            return
        if CANAIS_MUSICA and msg.channel.id not in CANAIS_MUSICA:
            return

        if eh_pedido(msg):
            self.banco.inserir_pedidos(
                [(msg.id, msg.channel.id, msg.author.id, (msg.content or "")[:500])]
            )
        else:
            musica = extrair_musica(msg)
            if not musica:
                return
            titulo, artista = musica
            self.banco.inserir([(msg.id, msg.channel.id, titulo, artista, msg.author.name)])

        # a marca ("última vez que guardou algo") só avança depois da sincronização do chat,
        # senão o que passou enquanto o bot estava desligado ficaria pra trás
        if msg.channel.id in self.sincronizados and msg.id > self.banco.marca(msg.channel.id):
            self.banco.salvar_marca(msg.channel.id, msg.id)

    async def ler_canal(self, canal, completo: bool):
        """Lê o canal e guarda músicas e pedidos. Retorna (musicas_achadas, pedidos_achados)."""
        marca = 0 if completo else self.banco.marca(canal.id)
        after = discord.Object(id=marca) if marca else None  # só o que é novo
        musicas_achadas = pedidos_achados = 0
        ultimo = marca
        lote_musicas, lote_pedidos = [], []

        async for msg in canal.history(limit=None, after=after, oldest_first=True):
            ultimo = msg.id

            if eh_pedido(msg):
                pedidos_achados += 1
                lote_pedidos.append((msg.id, canal.id, msg.author.id, (msg.content or "")[:500]))
            else:
                musica = extrair_musica(msg)
                if musica:
                    titulo, artista = musica
                    musicas_achadas += 1
                    lote_musicas.append((msg.id, canal.id, titulo, artista, msg.author.name))

            if len(lote_musicas) >= LOTE:
                self.banco.inserir(lote_musicas)
                lote_musicas = []
            if len(lote_pedidos) >= LOTE:
                self.banco.inserir_pedidos(lote_pedidos)
                lote_pedidos = []

        if lote_musicas:
            self.banco.inserir(lote_musicas)
        if lote_pedidos:
            self.banco.inserir_pedidos(lote_pedidos)
        if ultimo:
            self.banco.salvar_marca(canal.id, ultimo)
        return musicas_achadas, pedidos_achados

    @commands.command(name="read")
    @commands.guild_only()
    @commands.is_owner()
    async def read(self, ctx: commands.Context, canais: commands.Greedy[discord.TextChannel], modo: str = ""):
        """(Só o dono) Lê o histórico e guarda músicas e pedidos no banco."""
        completo = modo.lower() in ("tudo", "completo", "full")

        if self.lock.locked():
            await ctx.send("⏳ Já tem uma leitura em andamento, espera ela terminar.")
            return

        async with self.lock:
            nao_achados = []
            if not canais and CANAIS_MUSICA:
                # só os chats configurados (aceita também o chat de texto de um canal de voz)
                for cid in CANAIS_MUSICA:
                    c = ctx.guild.get_channel(cid)
                    if c is not None and hasattr(c, "history"):
                        canais.append(c)
                    else:
                        nao_achados.append(str(cid))
                if not canais:
                    await ctx.send(f"❌ Não achei nenhum dos chats configurados: {', '.join(nao_achados)}")
                    return
            elif not canais:
                eu = ctx.guild.me
                canais = [
                    c for c in list(ctx.guild.text_channels) + list(ctx.guild.voice_channels)
                    if c.permissions_for(eu).view_channel
                    and c.permissions_for(eu).read_message_history
                ]

            musicas_antes, pedidos_antes = self.banco.total(), self.banco.total_pedidos()
            musicas_achadas = pedidos_achados = 0
            sem_acesso = []
            status = await ctx.send("📖 Começando a leitura...")

            for i, canal in enumerate(canais, start=1):
                await status.edit(content=f"📖 Lendo {canal.mention} ({i}/{len(canais)})...")
                try:
                    m, p = await self.ler_canal(canal, completo)
                    musicas_achadas += m
                    pedidos_achados += p
                except discord.Forbidden:
                    sem_acesso.append(canal.mention)

            musicas_depois, pedidos_depois = self.banco.total(), self.banco.total_pedidos()
            musicas_novas = musicas_depois - musicas_antes
            pedidos_novos = pedidos_depois - pedidos_antes
            resumo = (
                "✅ **Leitura concluída!**\n"
                f"🎵 Músicas: **{musicas_achadas}** encontradas • **{musicas_novas}** novas • "
                f"**{musicas_achadas - musicas_novas}** já estavam salvas • total **{musicas_depois}**\n"
                f"🎧 Pedidos (m!play/p!play): **{pedidos_achados}** encontrados • **{pedidos_novos}** novos • "
                f"**{pedidos_achados - pedidos_novos}** já estavam salvos • total **{pedidos_depois}**\n"
                f"Use `{PREFIX}musicas` pra ver o ranking."
            )
            if sem_acesso:
                resumo += f"\n⚠️ Sem acesso a: {', '.join(sem_acesso)}"
            if nao_achados:
                resumo += f"\n⚠️ IDs que não achei no servidor: {', '.join(nao_achados)}"
            await status.edit(content=resumo)

    @commands.command(name="musicas", aliases=["músicas", "ranking"])  # "ranking" continua funcionando (nome antigo)
    async def musicas(self, ctx: commands.Context, *args: str):
        """Rankings guardados (não lê o chat): geral, artista ou ios, com período opcional."""
        tipo, periodo = "", None
        conhecidos = NOMES_RANKING_MUSICA + NOMES_RANKING_ARTISTA + NOMES_RANKING_USUARIO
        for palavra in (a.lower() for a in args):
            if palavra in PERIODOS:
                periodo = palavra
            elif palavra in conhecidos:
                tipo = palavra
            else:
                await ctx.send(
                    f"Não conheço esse ranking. Use `{PREFIX}musicas`, `{PREFIX}musicas artista` "
                    f"ou `{PREFIX}musicas ios`, e se quiser junte com `semana`, `mes` ou `ano`."
                )
                return

        desde, rotulo = 0, ""
        if periodo:
            rotulo, dias = PERIODOS[periodo]
            desde = discord.utils.time_snowflake(discord.utils.utcnow() - timedelta(days=dias))

        if tipo in NOMES_RANKING_MUSICA:
            linhas = self.banco.ranking_musicas(desde)
            titulo = "🏆 Ranking de músicas mais tocadas"
            itens = [
                (f"{t} by {a}"[:80] if a else t[:80]) + f" ({q}x)"
                for t, a, q in linhas
            ]
            rodape = f"{sum(q for _, _, q in linhas)} tocadas • {len(linhas)} músicas"

        elif tipo in NOMES_RANKING_ARTISTA:
            linhas = self.banco.ranking_artistas(desde)
            titulo = "🎤 Ranking de artistas"
            itens = [
                f"{a[:70]} — {n} {'música' if n == 1 else 'músicas'}"
                for a, n, _ in linhas
            ]
            rodape = f"{len(linhas)} artistas"

        else:  # NOMES_RANKING_USUARIO
            linhas = self.banco.ranking_usuarios(desde)
            titulo = "🎧 Quem mais pediu música"
            itens = [f"<@{uid}> — {q} {'pedido' if q == 1 else 'pedidos'}" for uid, q in linhas]
            rodape = f"{sum(q for _, q in linhas)} pedidos • {len(linhas)} pessoas"

        if periodo:
            titulo += f" — {rotulo}"

        if not linhas:
            if periodo:
                await ctx.send(f"Não tem nada guardado nos {rotulo} 🤔")
            else:
                await ctx.send("Ainda não tem nada guardado pra esse ranking 🤔 (o dono precisa rodar "
                               f"`{PREFIX}read tudo`)")
            return

        # capa do álbum da música #1 (ou de uma música do artista #1); o ranking de pessoas fica sem capa
        capa = ""
        if tipo in NOMES_RANKING_MUSICA or tipo in NOMES_RANKING_ARTISTA:
            async with ctx.typing():
                if tipo in NOMES_RANKING_MUSICA:
                    t, a, _ = linhas[0]
                else:
                    top = self.banco.musica_top_do_artista(linhas[0][0], desde)
                    t, a = top if top else ("", "")
                if t:
                    capa, _ = await self.deezer.info_seguro(f"{t} {a}".strip(), t, a)

        view = RankingView(ctx.author.id, titulo, itens, rodape, capa)
        view.message = await ctx.send(embed=view.montar_embed(), view=view)

    # ----- mm!perfil ---------------------------------------------------------
    @commands.command(name="perfil")
    @commands.guild_only()
    async def perfil(self, ctx: commands.Context, pessoa: discord.User = None):
        """Perfil de alguém (você, se não marcar ninguém): pedidos, mensagens e música mais pedida."""
        pessoa = pessoa or ctx.author
        ranking = self.banco.ranking_usuarios()
        posicao = next((i for i, (uid, _) in enumerate(ranking, start=1) if uid == pessoa.id), None)
        atividade = self.banco.atividade_da_pessoa(pessoa.id)  # vem do mm!scan
        scan_em = self.banco.quando_scan()
        if posicao is None and atividade is None:
            await ctx.send(f"**{pessoa.display_name}** ainda não tem nada guardado 🤔")
            return

        # música mais pedida (o que a pessoa mais digitou depois do m!play/p!play)
        consultas, exemplo = Counter(), {}
        for _, conteudo in self.banco.pedidos_do_usuario(pessoa.id):
            consulta = consulta_do_pedido(conteudo)
            if consulta:
                chave = normalizar(consulta)
                consultas[chave] += 1
                exemplo.setdefault(chave, " ".join(consulta.split()))
        if consultas:
            chave, vezes = consultas.most_common(1)[0]
            async with ctx.typing():
                achado = await self.links.titulo_seguro(exemplo[chave])  # link vira o nome do vídeo/música
            mais_pedida = f"{formatar_consulta(exemplo[chave], achado, 70)} — **{vezes}x**"
        elif posicao is not None:
            mais_pedida = f"(texto dos pedidos não guardado — rode `{PREFIX}read tudo`)"
        else:
            mais_pedida = "—"

        if posicao is not None:
            pedidos_txt = f"**{ranking[posicao - 1][1]}**\n{posicao_texto(posicao, len(ranking))}"
        else:
            pedidos_txt = "**0**\nsem ranking"
        if atividade:
            mensagens, pos_msg, total_msg = atividade
            mensagens_txt = f"**{milhar(mensagens)}**\n{posicao_texto(pos_msg, total_msg)}"
        elif scan_em:
            mensagens_txt = "**0**\nsem ranking"
        else:
            mensagens_txt = "**—**\nainda não contadas"

        embed = discord.Embed(title=pessoa.display_name, color=0x5865F2)
        embed.set_thumbnail(url=pessoa.display_avatar.with_size(256).url)  # foto pequena, no canto
        embed.add_field(name="🎧 Pedidos", value=pedidos_txt, inline=True)
        embed.add_field(name="💬 Mensagens", value=mensagens_txt, inline=True)
        embed.add_field(name="🎵 Música mais pedida", value=mais_pedida, inline=False)
        rodape = BOT_NAME
        if scan_em:
            rodape += " • mensagens contadas em " + datetime.fromtimestamp(scan_em, FUSO).strftime("%d/%m/%Y")
        embed.set_footer(text=rodape)
        await ctx.send(embed=embed)

    # ----- mm!aleatoria ------------------------------------------------------
    @commands.command(name="aleatoria", aliases=["aleatória", "random"])
    @commands.guild_only()
    async def aleatoria(self, ctx: commands.Context):
        """Sorteia uma música do histórico e mostra o m!play pronto pra copiar."""
        sorteada = self.banco.musica_aleatoria()
        if not sorteada:
            await ctx.send("Ainda não tem música guardada 🤔 (o dono precisa rodar "
                           f"`{PREFIX}read tudo`)")
            return

        titulo, artista, vezes = sorteada
        busca = f"{titulo} {artista}".strip().replace("`", "'")
        comando = f"{COMANDO_PLAY} {busca}"[:300]

        async with ctx.typing():
            capa, genero = await self.deezer.info_seguro(f"{titulo} {artista}".strip(), titulo, artista)

        embed = discord.Embed(
            title="🎲 Música sorteada",
            description=(
                f"**{titulo}**" + (f"\n{artista}" if artista else "")
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
            await ctx.send(f"**{autor.display_name}**, você não tem pedidos nos últimos 12 meses 🤔")
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
        linhas_grafico = []
        a, m = inicio.year, inicio.month
        for _ in range(12):
            n = por_mes[(a, m)]
            barra = "█" * max(1, round(10 * n / maior)) if n else "·"
            linhas_grafico.append(f"{MESES[m - 1]}/{str(a)[2:]} {barra:<10} {n}")
            m += 1
            if m > 12:
                m, a = 1, a + 1

        (a_top, m_top), n_top = por_mes.most_common(1)[0]
        dia_top = por_dia_semana.most_common(1)[0][0]

        ranking = self.banco.ranking_usuarios(desde)
        posicao = next((i for i, (uid, _) in enumerate(ranking, start=1) if uid == autor.id), None)

        # capa e gênero favorito: procura no Deezer os pedidos mais feitos
        top_consultas = consultas.most_common(WRAPPED_TOP_GENERO)
        capa, generos = "", Counter()
        top5 = top_consultas[:5]
        achados = [None] * len(top5)  # títulos dos links (YouTube/Spotify) do top 5
        if top_consultas:
            async with ctx.typing():
                infos, achados = await asyncio.gather(
                    asyncio.gather(*(self.deezer.info_seguro(exemplo[ch]) for ch, _ in top_consultas)),
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
        embed = discord.Embed(title=f"🎁 Wrapped de {autor.display_name}", description=descricao, color=0xEB459E)
        embed.set_author(name=autor.display_name, icon_url=autor.display_avatar.with_size(128).url)
        if capa:
            embed.set_thumbnail(url=capa)
        embed.add_field(name="🎧 Pedidos", value=f"**{len(pedidos)}**", inline=True)
        embed.add_field(name="📅 Dias com pedido", value=f"**{len(dias)}**", inline=True)
        if posicao:
            embed.add_field(name="🏆 Posição", value=f"**#{posicao}** de {len(ranking)}", inline=True)
        embed.add_field(name="🔥 Mês mais ativo", value=f"{MESES[m_top - 1]}/{a_top} ({n_top})", inline=True)
        embed.add_field(name="🗓️ Dia favorito", value=DIAS_SEMANA[dia_top], inline=True)
        if generos:
            mais = generos.most_common(3)
            valor = f"**{mais[0][0]}** ({mais[0][1]})" + "".join(f"\n{g} ({n})" for g, n in mais[1:])
            embed.add_field(name="🎼 Gênero favorito", value=valor, inline=True)
        if consultas:
            top = [
                f"**{i}.** {formatar_consulta(exemplo[ch], achado)} — {q}x"
                for i, ((ch, q), achado) in enumerate(zip(top5, achados), start=1)
            ]
            embed.add_field(name="🎵 Mais pedidos", value="\n".join(top), inline=False)
        embed.add_field(name="📈 Mês a mês", value="```\n" + "\n".join(linhas_grafico) + "\n```", inline=False)
        embed.set_footer(text=BOT_NAME)
        await ctx.send(embed=embed)

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

    @commands.command(name="exportar", aliases=["export", "csv"])
    @commands.guild_only()
    @commands.cooldown(1, 30, commands.BucketType.user)
    async def exportar(self, ctx: commands.Context):
        """Manda tudo que o bot guardou em planilhas CSV."""
        async with ctx.typing():
            tocadas = self.banco.historico_tocadas()
            pedidos = self.banco.historico_pedidos()
            if not tocadas and not pedidos:
                await ctx.send("Ainda não tem nada guardado pra exportar 🤔 (o dono precisa rodar "
                               f"`{PREFIX}read tudo`)")
                return

            musicas = self.banco.ranking_musicas()
            artistas = self.banco.ranking_artistas()
            usuarios = self.banco.ranking_usuarios()
            nomes = await self.resolver_nomes([uid for uid, _ in usuarios])

            def hora(message_id):
                return data_local(message_id).strftime("%d/%m/%Y %H:%M:%S")

            arquivos = [
                discord.File(gerar_csv(
                    ("posicao", "titulo", "artista", "vezes_tocada"),
                    [(i, t, a, q) for i, (t, a, q) in enumerate(musicas, start=1)],
                ), filename="ranking_musicas.csv"),
                discord.File(gerar_csv(
                    ("posicao", "artista", "musicas_diferentes", "vezes_tocadas"),
                    [(i, a, n, q) for i, (a, n, q) in enumerate(artistas, start=1)],
                ), filename="ranking_artistas.csv"),
                discord.File(gerar_csv(
                    ("posicao", "usuario_id", "nome", "pedidos"),
                    [(i, uid, nomes.get(uid, ""), q) for i, (uid, q) in enumerate(usuarios, start=1)],
                ), filename="ranking_usuarios.csv"),
                discord.File(gerar_csv(
                    ("data_hora", "titulo", "artista", "bot", "canal_id"),
                    [(hora(mid), t, a, b or "", cid) for mid, t, a, b, cid in tocadas],
                ), filename="historico_tocadas.csv"),
                discord.File(gerar_csv(
                    ("data_hora", "usuario_id", "nome", "pedido", "canal_id"),
                    [
                        (hora(mid), uid, nomes.get(uid, ""), consulta_do_pedido(c) or "", cid)
                        for mid, uid, c, cid in pedidos
                    ],
                ), filename="historico_pedidos.csv"),
            ]

        try:
            await ctx.send(
                f"📊 **Planilhas prontas!** {len(tocadas)} músicas tocadas • {len(pedidos)} pedidos.\n"
                "Separador `;` e horários em Brasília. O Excel abre direto; no Google Planilhas use "
                "Arquivo → Importar.",
                files=arquivos,
            )
        except discord.HTTPException:
            await ctx.send("❌ Não consegui enviar: os arquivos passaram do limite de tamanho do Discord.")


# ===== Funcionalidade 2: quem mais fala ====================================
class Atividade(commands.Cog):
    """mm!scan (só dono, escondido) conta as mensagens de todos os chats; mm!tagarelas mostra o ranking."""

    def __init__(self, bot):
        self.bot = bot
        self.banco = bot.get_cog("Musicas").banco  # mesmo banco do cog de músicas
        self.lock = asyncio.Lock()

    @staticmethod
    def canais_legiveis(guild):
        eu = guild.me
        candidatos = list(guild.text_channels) + list(guild.voice_channels) + list(guild.threads)
        return [
            c for c in candidatos
            if c.permissions_for(eu).view_channel and c.permissions_for(eu).read_message_history
        ]

    @commands.command(name="scan", hidden=True)
    @commands.guild_only()
    @commands.is_owner()
    async def scan(self, ctx: commands.Context):
        """(Só o dono) Lê TODOS os chats e conta quantas mensagens cada pessoa/bot mandou."""
        if self.lock.locked():
            await ctx.send("⏳ Já tem um scan em andamento, espera ele terminar.")
            return

        async with self.lock:
            canais = self.canais_legiveis(ctx.guild)
            contagem, eh_bot = Counter(), {}
            total, sem_acesso = 0, []
            status = await ctx.send(f"🔎 Começando o scan de {len(canais)} chats...")
            ultima_edicao = 0.0

            async def atualizar(texto):
                nonlocal ultima_edicao
                if time.monotonic() - ultima_edicao < 5:  # o Discord limita edições seguidas
                    return
                ultima_edicao = time.monotonic()
                try:
                    await status.edit(content=texto)
                except discord.HTTPException:
                    pass

            for i, canal in enumerate(canais, start=1):
                await atualizar(f"🔎 Lendo {canal.mention} ({i}/{len(canais)}) • {total} mensagens até agora...")
                try:
                    async for msg in canal.history(limit=None):
                        contagem[msg.author.id] += 1
                        eh_bot[msg.author.id] = msg.author.bot
                        total += 1
                        if total % 1000 == 0:
                            await atualizar(
                                f"🔎 Lendo {canal.mention} ({i}/{len(canais)}) • {total} mensagens até agora..."
                            )
                except discord.Forbidden:
                    sem_acesso.append(canal.mention)

            if not total:
                await status.edit(content="Não achei nenhuma mensagem pra contar 🤔")
                return

            self.banco.substituir_atividade(contagem, eh_bot, int(time.time()))

            def melhor(bot_flag):
                return next(((uid, n) for uid, n in contagem.most_common() if eh_bot[uid] == bot_flag), None)

            pessoa, robo = melhor(False), melhor(True)
            resumo = (
                f"✅ **Scan concluído!** {total} mensagens em {len(canais)} chats • {len(contagem)} autores\n"
                + (f"🏆 Pessoa que mais falou: <@{pessoa[0]}> — **{pessoa[1]}** mensagens\n" if pessoa else "")
                + (f"🤖 Bot que mais falou: <@{robo[0]}> — **{robo[1]}** mensagens\n" if robo else "")
                + f"Veja o ranking com `{PREFIX}tagarelas`."
            )
            if sem_acesso:
                resumo += f"\n⚠️ Sem acesso a: {', '.join(sem_acesso)}"
            await status.edit(content=resumo, allowed_mentions=discord.AllowedMentions.none())

    @commands.command(name="tagarelas")
    async def tagarelas(self, ctx: commands.Context, tipo: str = ""):
        """Quem mais mandou mensagem (dados do último mm!scan): geral, ios (pessoas) ou bot."""
        tipo = tipo.lower()
        if tipo in NOMES_TAGARELAS_GERAL:
            filtro, titulo, quem = None, "💬 Quem mais mandou mensagem", "autores"
        elif tipo in NOMES_RANKING_USUARIO:
            filtro, titulo, quem = False, "🧑 Pessoas que mais mandaram mensagem", "pessoas"
        elif tipo in NOMES_TAGARELAS_BOT:
            filtro, titulo, quem = True, "🤖 Bots que mais mandaram mensagem", "bots"
        else:
            await ctx.send(f"Não conheço esse ranking. Use `{PREFIX}tagarelas`, `{PREFIX}tagarelas ios` "
                           f"ou `{PREFIX}tagarelas bot`.")
            return

        linhas = self.banco.ranking_atividade(filtro)
        if not linhas:
            await ctx.send("Ainda não tem contagem de mensagens 🤔 (o dono precisa rodar "
                           f"`{PREFIX}scan`)")
            return

        itens = [f"<@{uid}> — {n} {'mensagem' if n == 1 else 'mensagens'}" for uid, n in linhas]
        contado = datetime.fromtimestamp(self.banco.quando_scan(), FUSO).strftime("%d/%m/%Y")
        rodape = f"{sum(n for _, n in linhas)} mensagens • {len(linhas)} {quem} • contado em {contado}"
        view = RankingView(ctx.author.id, titulo, itens, rodape)
        view.message = await ctx.send(embed=view.montar_embed(), view=view)


# ===== Ajuda ===============================================================
class Ajuda(commands.Cog):
    """mm!help: lista de comandos pra todo mundo (comandos do dono ficam de fora)."""

    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="help", aliases=["ajuda", "comandos"])
    async def ajuda(self, ctx: commands.Context):
        embed = discord.Embed(
            title=f"📖 Comandos do {BOT_NAME}",
            description=(
                f"`{PREFIX}musicas` — músicas mais tocadas (também `artista` e `ios`)\n"
                f"`{PREFIX}musicas semana|mes|ano` — ranking por período\n"
                f"`{PREFIX}perfil [@pessoa]` — perfil da pessoa\n"
                f"`{PREFIX}aleatoria` — sorteia uma música (com capa) e mostra o `m!play` pra copiar\n"
                f"`{PREFIX}wrapped` — seu resumo dos últimos 12 meses\n"
                f"`{PREFIX}tagarelas` — quem mais mandou mensagem (`ios` = pessoas, `bot` = bots)"
            ),
            color=0x5865F2,
        )
        embed.set_footer(text=BOT_NAME)
        await ctx.send(embed=embed)


# Adicione novas funcionalidades aqui (cada uma como um Cog separado)
COGS = [Musicas, Atividade, Ajuda]  # Atividade usa o banco do Musicas: vem depois dele


class MeMiBot(commands.Bot):
    async def setup_hook(self):
        for cog in COGS:
            await self.add_cog(cog(self))

    async def on_ready(self):
        await self.change_presence(activity=discord.Game(name=f"{PREFIX}help"))
        print(f"{BOT_NAME} online como {self.user}")

    async def on_command_error(self, ctx, error):
        if isinstance(error, commands.CommandNotFound):
            return
        if isinstance(error, commands.NotOwner):
            await ctx.send("🔒 Só o dono do bot pode usar esse comando.")
            return
        if isinstance(error, commands.NoPrivateMessage):
            await ctx.send("Esse comando só funciona dentro de um servidor.")
            return
        if isinstance(error, commands.CommandOnCooldown):
            await ctx.send(f"⏳ Calma! Tenta de novo em {error.retry_after:.0f}s.")
            return
        if isinstance(error, commands.BadArgument):
            await ctx.send(f"Não entendi esse argumento 🤔 Veja `{PREFIX}help`.")
            return
        await super().on_command_error(ctx, error)


intents = discord.Intents.default()
intents.message_content = True  # ative também no Developer Portal!


def prefixo(bot, message):
    """Aceita o prefixo em qualquer combinação de maiúsculas/minúsculas (MM!, Mm!, mm!...)."""
    prefixos = commands.when_mentioned(bot, message)
    inicio = (message.content or "")[:len(PREFIX)]
    if inicio.lower() == PREFIX.lower():
        prefixos.append(inicio)  # devolve exatamente o que a pessoa digitou
    return prefixos


# case_insensitive: também aceita o nome do comando em maiúsculas (mm!RANKING, mm!Perfil...)
bot = MeMiBot(command_prefix=prefixo, case_insensitive=True, intents=intents, help_command=None)

if __name__ == "__main__":
    if not TOKEN or TOKEN == "COLE_SEU_TOKEN_AQUI":
        raise SystemExit(
            'Falta o token! Abra o arquivo "token.txt" (na mesma pasta do bot) '
            "e troque COLE_SEU_TOKEN_AQUI pelo seu token (só o token, sem aspas)."
        )
    bot.run(TOKEN)
