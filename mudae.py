"""Mudae tracker do MeMi BOT: lê as mensagens do Mudae e guarda rolls, casamentos e kakera.

Só leitura: o bot nunca interage com o Mudae. Como o Mudae funciona (o que este módulo espera):

- Roll: resposta a $w/$h/$m (e variações a/g/x) ou /wa etc. É uma mensagem solta, com um embed:
  personagem no autor do embed, série na 1ª linha da descrição, "Claims: #N"/"Likes: #N", o valor
  em kakera e, se ninguém tem o personagem, "Reaja com qualquer emoji para casar!". Se alguém tem,
  o rodapé diz "Pertence a X". Não é uma resposta ao comando nem menciona quem rolou:
  * comando de barra: o Discord informa quem usou (interaction_metadata);
  * comando "$": o roll é ligado ao comando "$" mais antigo ainda sem resposta, no mesmo canal,
    nos JANELA_COMANDO segundos anteriores (as respostas saem na ordem dos comandos). Recusas do
    Mudae ("**fulano**, a roleta está limitada...") também consomem o comando de quem foi recusado.
- Casamento: mensagem "💖 **X** e **Personagem** agora são casados! 💖" (também em inglês e
  espanhol). É ligada ao roll mais recente desse personagem no canal (JANELA_CASAMENTO); casar com
  o roll de outra pessoa é um "snipe".
- Kakera: "<:kakeraY:…> **X +401** ($k)".

O Mudae mostra as pessoas pelo nome, não pelo ID: o nome é resolvido pelos nomes já vistos no
servidor (usuário, nome global e apelido). Nome ambíguo ou desconhecido fica sem ID (a estatística
por pessoa ignora esse evento, a do servidor não). Mensagens com texto personalizado pelo Mudae
($renameclaim) não são reconhecidas.
"""

import re
import unicodedata

MUDAE_ID = 432610292342587392
JANELA_COMANDO = 10  # segundos entre o comando "$" e a resposta do Mudae
JANELA_CASAMENTO = 120  # segundos entre o roll e o anúncio do casamento
MS_SNOWFLAKE = 1 << 22  # 1 ms em unidades de ID do Discord
EPOCA_DISCORD = 1420070400000

SQL_TABELAS = """
CREATE TABLE IF NOT EXISTS mudae_nomes (
    chave TEXT NOT NULL, usuario_id INTEGER NOT NULL, PRIMARY KEY (chave, usuario_id));
CREATE TABLE IF NOT EXISTS mudae_rolls (
    message_id INTEGER PRIMARY KEY, canal_id INTEGER NOT NULL, comando_id INTEGER,
    roletador_id INTEGER, personagem TEXT NOT NULL, chave TEXT NOT NULL,
    serie TEXT NOT NULL DEFAULT '', claims INTEGER, likes INTEGER, kakera INTEGER,
    livre INTEGER NOT NULL DEFAULT 0, dono TEXT NOT NULL DEFAULT '');
CREATE INDEX IF NOT EXISTS idx_mudae_rolls_chave ON mudae_rolls(chave, message_id);
CREATE INDEX IF NOT EXISTS idx_mudae_rolls_roletador ON mudae_rolls(roletador_id);
CREATE TABLE IF NOT EXISTS mudae_consumidos (
    comando_id INTEGER PRIMARY KEY, resposta_id INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS idx_mudae_consumidos_resposta ON mudae_consumidos(resposta_id);
CREATE TABLE IF NOT EXISTS mudae_casamentos (
    message_id INTEGER PRIMARY KEY, canal_id INTEGER NOT NULL, usuario_id INTEGER,
    nome TEXT NOT NULL, personagem TEXT NOT NULL, chave TEXT NOT NULL, roll_id INTEGER,
    roletador_id INTEGER, kakera INTEGER);
CREATE TABLE IF NOT EXISTS mudae_kakera (
    message_id INTEGER NOT NULL, ordem INTEGER NOT NULL, usuario_id INTEGER, nome TEXT NOT NULL,
    tipo TEXT NOT NULL, valor INTEGER NOT NULL, PRIMARY KEY (message_id, ordem));
CREATE TABLE IF NOT EXISTS progresso_mudae (
    channel_id INTEGER PRIMARY KEY, ultimo_msg_id INTEGER NOT NULL);
"""

RE_PAGINA = re.compile(r"\b\d+\s*/\s*\d+\b")
RE_CLAIMS = re.compile(r"Claims?:\s*#\s*([\d.,]+)", re.I)
RE_LIKES = re.compile(r"Likes?:\s*#\s*([\d.,]+)", re.I)
RE_VALOR = re.compile(r"\*{0,2}([\d.,]+)\*{0,2}\s*<a?:kakera\w*:\d+>", re.I)
RE_LIVRE = re.compile(r"(para casar|to claim|para casarte|pour (?:vous )?marier)", re.I)
RE_DONO = re.compile(
    r"(?:belongs to|pertence a|pertenece a|appartient [àa])\s+(.+?)\s*$", re.I | re.S
)
_CASADOS = (
    r"(?:agora s[ãa]o casados|est[ãa]o agora casados|agora est[ãa]o casados|are now married"
    r"|ahora est[áa]n casados|est[áa]n ahora casados|sont (?:maintenant |d[ée]sormais )?mari[ée]s)"
)
RE_CASAMENTO = re.compile(
    rf"\*\*(?P<dono>[^*]+?)\*\*\s+(?:e|and|y|et)\s+\*\*(?P<char>[^*]+?)\*\*\s+{_CASADOS}", re.I
)
RE_CASAMENTO_SIMPLES = re.compile(
    rf"^(?P<dono>.+?)\s+(?:e|and|y|et)\s+(?P<char>.+?)\s+{_CASADOS}", re.I
)
RE_KAKERA = re.compile(
    r"<a?:(?P<tipo>kakera[A-Za-z0-9_]*):\d+>\s*"
    r"(?:breaks down into\s*<a?:kakera[A-Za-z0-9_]*:\d+>"
    r"(?:\s*\+\s*<a?:kakera[A-Za-z0-9_]*:\d+>)*\s*=>\s*)?"
    r"(?:\(\s*Free\s*\)\s*)?"
    r"\*{0,2}\s*(?P<nome>[^*\r\n+]+?)\s*\+\s*(?P<valor>[\d,.\s]+?)\s*\*{0,2}\s*\(\s*\$k\s*\)",
    re.I,
)
RE_RECUSA = re.compile(r"^\*\*(?P<nome>[^*]+?)\*\*\s*,")
RE_MENCAO = re.compile(r"<@!?(\d+)>")


# ------------------------------------------------------------------ leitura das mensagens
def eh_do_mudae(msg):
    autor = msg.author
    return bool(autor.bot) and (
        autor.id == MUDAE_ID or (getattr(autor, "name", "") or "").casefold() == "mudae"
    )


def chave(texto):
    """Nome normalizado para agrupar personagens e resolver pessoas (sem acento/maiúsculas)."""
    texto = unicodedata.normalize("NFKD", str(texto or "").casefold())
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", texto).strip()


def _nome_pessoa(texto):
    return re.sub(r"\s+", " ", str(texto or "").casefold()).strip()


def _limpar(texto):
    texto = re.sub(r"<a?:\w+:\d+>", "", texto or "")
    return re.sub(r"[*_~`|]", "", texto).strip()


def _numero(texto):
    digitos = re.sub(r"[^\d]", "", texto or "")
    return int(digitos) if digitos else None


def ler_roll(msg):
    """Dados do roll se `msg` é um roll do Mudae; senão None."""
    if not eh_do_mudae(msg) or len(msg.embeds) != 1:
        return None
    e = msg.embeds[0]
    personagem = _limpar(getattr(e.author, "name", None) or "")
    if not personagem or not getattr(e.image, "url", None) or getattr(e.thumbnail, "url", None):
        return None
    rodape = getattr(e.footer, "text", None) or ""
    if RE_PAGINA.search(rodape):  # $im e listas paginadas
        return None
    descricao = e.description or ""
    linhas = [x.strip() for x in descricao.splitlines() if x.strip()]
    serie = _limpar(linhas[0]) if linhas else ""
    if (
        RE_CLAIMS.search(serie)
        or RE_LIKES.search(serie)
        or RE_VALOR.search(linhas[0] if linhas else "")
    ):
        serie = ""
    achado = {
        nome: rx.search(descricao)
        for nome, rx in (("claims", RE_CLAIMS), ("likes", RE_LIKES), ("kakera", RE_VALOR))
    }
    dono = ""
    for parte in rodape.split("·"):
        m = RE_DONO.search(parte)
        if m:
            dono = _limpar(m.group(1)).rstrip(".").strip()
    return {
        "personagem": personagem[:200],
        "serie": serie[:200],
        "claims": _numero(achado["claims"].group(1)) if achado["claims"] else None,
        "likes": _numero(achado["likes"].group(1)) if achado["likes"] else None,
        "kakera": _numero(achado["kakera"].group(1)) if achado["kakera"] else None,
        "livre": bool(RE_LIVRE.search(descricao)) and not dono,
        "dono": dono[:100],
    }


def ler_casamento(texto):
    """(nome de quem casou, personagem) de um anúncio de casamento; senão None."""
    for linha in (texto or "").splitlines():
        linha = linha.strip()
        if not linha or linha.startswith(">"):
            continue
        m = RE_CASAMENTO.search(linha)
        if not m:
            m = RE_CASAMENTO_SIMPLES.search(_limpar(linha.replace("💖", "")))
        if m:
            dono, char = _limpar(m.group("dono")), _limpar(m.group("char"))
            if dono and char:
                return dono, char
    return None


def ler_kakera(texto):
    """[(nome, tipo, valor)] das coletas de kakera na mensagem."""
    return [
        (_limpar(m.group("nome")), m.group("tipo"), _numero(m.group("valor")) or 0)
        for m in RE_KAKERA.finditer(texto or "")
    ]


def nome_da_recusa(texto):
    """Nome em '**fulano**, ...' (respostas do Mudae a um comando que não rolou); senão None."""
    if ler_casamento(texto):
        return None
    m = RE_RECUSA.match((texto or "").strip())
    return _limpar(m.group("nome")) if m else None


def _quem_interagiu(msg):
    for campo in ("interaction_metadata", "interaction"):
        usuario = getattr(getattr(msg, campo, None), "user", None)
        if usuario is not None and getattr(usuario, "id", None):
            return usuario.id
    return None


def tipo_de(msg):
    """Como o tracker entende a mensagem, sem gravar nada (diagnóstico do mm!mudaedump)."""
    if not eh_do_mudae(msg):
        return None
    if ler_roll(msg):
        return "roll"
    texto = msg.content or ""
    if ler_casamento(texto):
        return "casamento"
    if ler_kakera(texto):
        return "kakera"
    if nome_da_recusa(texto):
        return "recusa"
    return None


# ------------------------------------------------------------------ banco
def criar_tabelas(con):
    con.executescript(SQL_TABELAS)


def registrar_nomes(con, autor):
    """Guarda os nomes (usuário, global e apelido) de uma pessoa para resolver o Mudae."""
    if getattr(autor, "bot", False):
        return
    for nome in {
        getattr(autor, "name", None),
        getattr(autor, "global_name", None),
        getattr(autor, "display_name", None),
    }:
        if nome and _nome_pessoa(nome):
            con.execute(
                "INSERT OR IGNORE INTO mudae_nomes VALUES (?,?)", (_nome_pessoa(nome), autor.id)
            )


def resolver_nome(con, nome):
    """ID de quem usa `nome` (menção, usuário, nome global ou apelido); None se ambíguo."""
    mencao = RE_MENCAO.fullmatch((nome or "").strip())
    if mencao:
        return int(mencao.group(1))
    alvo = _nome_pessoa(nome)
    if not alvo:
        return None
    ids = {r[0] for r in con.execute("SELECT usuario_id FROM mudae_nomes WHERE chave=?", (alvo,))}
    if not ids:
        # Sem nome visto nas mensagens: tenta os apelidos guardados pelo contador de mensagens
        # (casefold no Python: o lower() do SQLite só conhece ASCII).
        ids = {
            uid
            for uid, nome in con.execute("SELECT usuario_id, nome FROM autores WHERE eh_bot=0")
            if _nome_pessoa(nome) == alvo
        }
    return ids.pop() if len(ids) == 1 else None


def _comando_pendente(con, canal_id, antes, usuario_id=None):
    inicio = antes - JANELA_COMANDO * 1000 * MS_SNOWFLAKE
    sql = (
        "SELECT m.message_id, m.usuario_id FROM mudae m "
        "JOIN mensagens g ON g.message_id=m.message_id "
        "LEFT JOIN mudae_consumidos c ON c.comando_id=m.message_id "
        "WHERE g.canal_id=? AND m.message_id<? AND m.message_id>=? AND c.comando_id IS NULL"
    )
    parametros = [canal_id, antes, inicio]
    if usuario_id is not None:
        sql += " AND m.usuario_id=?"
        parametros.append(usuario_id)
    return con.execute(sql + " ORDER BY m.message_id LIMIT 1", parametros).fetchone()


def registrar(con, msg):
    """Guarda o que a mensagem do Mudae representa. Devolve 'roll', 'casamento', 'kakera',
    'recusa' ou None (não reconhecida ou já registrada). Chame dentro de uma transação."""
    if not eh_do_mudae(msg):
        return None
    canal = msg.channel.id
    roll = ler_roll(msg)
    if roll:
        if con.execute("SELECT 1 FROM mudae_rolls WHERE message_id=?", (msg.id,)).fetchone():
            return None
        roletador, comando = _quem_interagiu(msg), None
        if roletador is None:
            pendente = _comando_pendente(con, canal, msg.id)
            if pendente:
                comando, roletador = pendente
                con.execute("INSERT INTO mudae_consumidos VALUES (?,?)", (comando, msg.id))
        con.execute(
            "INSERT INTO mudae_rolls VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                msg.id,
                canal,
                comando,
                roletador,
                roll["personagem"],
                chave(roll["personagem"]),
                roll["serie"],
                roll["claims"],
                roll["likes"],
                roll["kakera"],
                int(roll["livre"]),
                roll["dono"],
            ),
        )
        return "roll"
    texto = msg.content or ""
    casou = ler_casamento(texto)
    if casou:
        if con.execute("SELECT 1 FROM mudae_casamentos WHERE message_id=?", (msg.id,)).fetchone():
            return None
        nome, personagem = casou
        inicio = msg.id - JANELA_CASAMENTO * 1000 * MS_SNOWFLAKE
        ligado = con.execute(
            "SELECT message_id, roletador_id, kakera FROM mudae_rolls WHERE canal_id=? AND chave=? "
            "AND message_id<? AND message_id>=? ORDER BY message_id DESC LIMIT 1",
            (canal, chave(personagem), msg.id, inicio),
        ).fetchone()
        roll_id, roletador, kakera = ligado or (None, None, None)
        con.execute(
            "INSERT INTO mudae_casamentos VALUES (?,?,?,?,?,?,?,?,?)",
            (msg.id, canal, resolver_nome(con, nome), nome[:100], personagem[:200],
             chave(personagem), roll_id, roletador, kakera),
        )  # fmt: skip
        if roll_id is not None:
            con.execute("UPDATE mudae_rolls SET dono=? WHERE message_id=?", (nome[:100], roll_id))
        return "casamento"
    coletas = ler_kakera(texto)
    if coletas:
        if con.execute("SELECT 1 FROM mudae_kakera WHERE message_id=?", (msg.id,)).fetchone():
            return None
        for ordem, (nome, tipo, valor) in enumerate(coletas):
            con.execute(
                "INSERT INTO mudae_kakera VALUES (?,?,?,?,?,?)",
                (msg.id, ordem, resolver_nome(con, nome), nome[:100], tipo, valor),
            )
        return "kakera"
    recusado = nome_da_recusa(texto)
    if recusado:
        if con.execute("SELECT 1 FROM mudae_consumidos WHERE resposta_id=?", (msg.id,)).fetchone():
            return None
        uid = resolver_nome(con, recusado)
        pendente = _comando_pendente(con, canal, msg.id, uid) if uid is not None else None
        if pendente:
            con.execute("INSERT INTO mudae_consumidos VALUES (?,?)", (pendente[0], msg.id))
            return "recusa"
    return None


def atualizar_roll(con, msg):
    """Aplica a edição de um roll (o Mudae põe o dono no rodapé ao casar). True se atualizou."""
    roll = ler_roll(msg)
    if not roll or not roll["dono"]:
        return False
    return bool(
        con.execute(
            "UPDATE mudae_rolls SET dono=? WHERE message_id=? AND dono<>?",
            (roll["dono"], msg.id, roll["dono"]),
        ).rowcount
    )


# ------------------------------------------------------------------ estatísticas
def _faixa(coluna, desde=0, ate=None):
    sql = f" {coluna}>=?"
    parametros = [desde]
    if ate is not None:
        sql += f" AND {coluna}<?"
        parametros.append(ate)
    return sql, parametros


def _hora_sql(coluna):
    return f"CAST(strftime('%H', (({coluna} >> 22)+{EPOCA_DISCORD})/1000, 'unixepoch', '-3 hours') AS INTEGER)"


def panorama(con, desde=0, ate=None):
    """Números do servidor: rolls, casamentos, kakera, tops, recordes e horários."""
    f, p = _faixa("message_id", desde, ate)
    um = lambda sql, *extra: con.execute(sql, [*p, *extra]).fetchone()  # noqa: E731
    rolls, livres = um(f"SELECT COUNT(*), COALESCE(SUM(livre),0) FROM mudae_rolls WHERE{f}")
    casamentos = um(f"SELECT COUNT(*) FROM mudae_casamentos WHERE{f}")[0]
    kakera = um(f"SELECT COALESCE(SUM(valor),0) FROM mudae_kakera WHERE{f}")[0]
    horas = [0] * 24
    for hora, n in con.execute(
        f"SELECT {_hora_sql('message_id')}, COUNT(*) FROM mudae_rolls WHERE{f} GROUP BY 1", p
    ):
        horas[int(hora)] = n
    return {
        "rolls": rolls,
        "livres": livres,
        "casamentos": casamentos,
        "kakera": kakera,
        "taxa": round(100 * casamentos / livres) if livres else 0,
        "personagens": [(n, q) for n, _, q in ranking(con, "personagens", desde, ate)[:5]],
        "series": ranking(con, "series", desde, ate)[:5],
        "maior_casamento": um(
            f"SELECT personagem, usuario_id, kakera FROM mudae_casamentos WHERE{f} "
            "AND kakera IS NOT NULL ORDER BY kakera DESC, message_id LIMIT 1"
        ),
        "escapou": um(
            f"SELECT personagem, kakera FROM mudae_rolls r WHERE{f} AND livre=1 "
            "AND kakera IS NOT NULL AND NOT EXISTS "
            "(SELECT 1 FROM mudae_casamentos c WHERE c.roll_id=r.message_id) "
            "ORDER BY kakera DESC, message_id LIMIT 1"
        ),
        "horas": horas,
    }


def perfil(con, uid):
    """Números de uma pessoa no Mudae."""
    um = lambda sql: con.execute(sql, (uid,)).fetchone()  # noqa: E731
    rolls = um("SELECT COUNT(*) FROM mudae_rolls WHERE roletador_id=?")[0]
    casamentos = um("SELECT COUNT(*) FROM mudae_casamentos WHERE usuario_id=?")[0]
    proprios = um(
        "SELECT COUNT(*) FROM mudae_casamentos WHERE usuario_id=? AND roletador_id=usuario_id"
    )[0]
    return {
        "rolls": rolls,
        "casamentos": casamentos,
        "aproveitamento": round(100 * proprios / rolls) if rolls else 0,
        "kakera": um("SELECT COALESCE(SUM(valor),0) FROM mudae_kakera WHERE usuario_id=?")[0],
        "favorito": um(
            "SELECT MIN(personagem), COUNT(*) FROM mudae_rolls WHERE roletador_id=? "
            "GROUP BY chave ORDER BY COUNT(*) DESC, MIN(message_id) LIMIT 1"
        ),
        "maior_casamento": um(
            "SELECT personagem, kakera FROM mudae_casamentos WHERE usuario_id=? "
            "AND kakera IS NOT NULL ORDER BY kakera DESC, message_id LIMIT 1"
        ),
        "snipes": um(
            "SELECT COUNT(*) FROM mudae_casamentos WHERE usuario_id=? "
            "AND roletador_id IS NOT NULL AND roletador_id<>usuario_id"
        )[0],
        "sofridos": um(
            "SELECT COUNT(*) FROM mudae_casamentos WHERE roletador_id=? "
            "AND usuario_id IS NOT NULL AND usuario_id<>roletador_id"
        )[0],
        "horas": [
            n
            for n in (
                dict(
                    con.execute(
                        f"SELECT {_hora_sql('message_id')}, COUNT(*) FROM mudae_rolls "
                        "WHERE roletador_id=? GROUP BY 1",
                        (uid,),
                    ).fetchall()
                ).get(h, 0)
                for h in range(24)
            )
        ],
    }


RANKINGS = ("casamentos", "kakera", "snipers", "personagens", "series", "azarados")


def ranking(con, tipo, desde=0, ate=None, minimo=50):
    """Rankings do Mudae. Pessoas: [(uid, n)]; personagens: [(nome, série, n)]; séries:
    [(série, n)]. `azarados` = rolls sem casar (quem tem pelo menos `minimo` rolls)."""
    f, p = _faixa("message_id", desde, ate)
    if tipo == "casamentos":
        sql = (
            f"SELECT usuario_id, COUNT(*) FROM mudae_casamentos WHERE{f} AND usuario_id IS NOT NULL "
            "GROUP BY usuario_id ORDER BY COUNT(*) DESC, MIN(message_id)"
        )
    elif tipo == "kakera":
        sql = (
            f"SELECT usuario_id, SUM(valor) FROM mudae_kakera WHERE{f} AND usuario_id IS NOT NULL "
            "GROUP BY usuario_id ORDER BY SUM(valor) DESC, MIN(message_id)"
        )
    elif tipo == "snipers":
        sql = (
            f"SELECT usuario_id, COUNT(*) FROM mudae_casamentos WHERE{f} AND usuario_id IS NOT NULL "
            "AND roletador_id IS NOT NULL AND roletador_id<>usuario_id "
            "GROUP BY usuario_id ORDER BY COUNT(*) DESC, MIN(message_id)"
        )
    elif tipo == "personagens":
        sql = (
            f"SELECT MIN(personagem), MIN(NULLIF(serie,'')), COUNT(*) FROM mudae_rolls WHERE{f} "
            "GROUP BY chave ORDER BY COUNT(*) DESC, MIN(message_id)"
        )
        return [(n, s or "", q) for n, s, q in con.execute(sql, p)]
    elif tipo == "series":
        sql = (
            f"SELECT MIN(serie), COUNT(*) FROM mudae_rolls WHERE{f} AND serie<>'' "
            "GROUP BY lower(serie) ORDER BY COUNT(*) DESC, MIN(message_id)"
        )
    elif tipo == "azarados":
        sql = (
            "SELECT r.roletador_id, COUNT(*) - COALESCE(("
            "SELECT COUNT(*) FROM mudae_casamentos c WHERE c.usuario_id=r.roletador_id "
            f"AND c.roletador_id=c.usuario_id AND{_faixa('c.message_id', desde, ate)[0]}), 0) AS azar "
            f"FROM mudae_rolls r WHERE{_faixa('r.message_id', desde, ate)[0]} "
            "AND r.roletador_id IS NOT NULL GROUP BY r.roletador_id "
            "HAVING COUNT(*)>=? AND azar>0 ORDER BY azar DESC, r.roletador_id"
        )
        return [tuple(x) for x in con.execute(sql, [*p, *p, minimo])]
    else:
        raise ValueError(f"ranking desconhecido: {tipo}")
    return [tuple(x) for x in con.execute(sql, p)]


def personagem(con, texto):
    """Tudo sobre um personagem (nome exato ou começo do nome, sem acento); None se não saiu."""
    alvo = chave(texto)
    if not alvo:
        return None
    linha = (
        con.execute("SELECT chave FROM mudae_rolls WHERE chave=? LIMIT 1", (alvo,)).fetchone()
        or con.execute(
            "SELECT chave FROM mudae_rolls WHERE chave LIKE ? ESCAPE '\\' "
            "GROUP BY chave ORDER BY COUNT(*) DESC LIMIT 1",
            (alvo.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%",),
        ).fetchone()
    )
    if not linha:
        return None
    k = linha[0]
    nome, serie, vezes, primeira, maior, melhor_rank = con.execute(
        "SELECT MIN(personagem), MIN(NULLIF(serie,'')), COUNT(*), MIN(message_id), MAX(kakera), "
        "MIN(claims) FROM mudae_rolls WHERE chave=?",
        (k,),
    ).fetchone()
    return {
        "nome": nome,
        "serie": serie or "",
        "vezes": vezes,
        "primeira": primeira,
        "maior_kakera": maior,
        "claims": melhor_rank,
        "roletadores": [
            tuple(x)
            for x in con.execute(
                "SELECT roletador_id, COUNT(*) FROM mudae_rolls WHERE chave=? AND roletador_id "
                "IS NOT NULL GROUP BY roletador_id ORDER BY COUNT(*) DESC, MIN(message_id) LIMIT 5",
                (k,),
            )
        ],
        "casamentos": [
            tuple(x)
            for x in con.execute(
                "SELECT usuario_id, nome FROM mudae_casamentos WHERE chave=? ORDER BY message_id",
                (k,),
            )
        ],
    }


def resumo(con, desde, ate):
    """Números do Mudae num período fechado (para o resumo do mês/ano)."""
    f, p = _faixa("message_id", desde, ate)
    rolls = con.execute(f"SELECT COUNT(*) FROM mudae_rolls WHERE{f}", p).fetchone()[0]
    casamentos = con.execute(f"SELECT COUNT(*) FROM mudae_casamentos WHERE{f}", p).fetchone()[0]
    top = ranking(con, "personagens", desde, ate)[:1]
    roletador = con.execute(
        f"SELECT t.usuario_id, COUNT(*) FROM mudae t LEFT JOIN autores a ON a.usuario_id=t.usuario_id "
        f"WHERE{_faixa('t.message_id', desde, ate)[0]} AND COALESCE(a.eh_bot,0)=0 "
        "GROUP BY t.usuario_id ORDER BY COUNT(*) DESC, MAX(t.message_id) LIMIT 1",
        p,
    ).fetchone()
    return {
        "rolls": rolls,
        "casamentos": casamentos,
        "personagem": [top[0][0], top[0][2]] if top else None,
        "roletador": list(roletador) if roletador else None,
    }
