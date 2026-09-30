"""Aparência das mensagens do MeMi BOT: cores, emojis e montagem de embeds.

Só depende do discord.py e não conhece banco nem comandos. Para trocar um emoji ou uma cor
do bot inteiro, edite as constantes abaixo (um emoji do servidor entra como "<:nome:ID>").
"""

import discord

BOT_NAME = "MeMi BOT"

COR_PADRAO = 0x5865F2
COR_SUCESSO = 0x57F287
COR_AVISO = 0xFEE75C
COR_ERRO = 0xED4245

EMOJI = {
    "sucesso": "✅",
    "aviso": "⚠️",
    "erro": "❌",
    "musica": "🎧",
    "mensagens": "💬",
    "mudae": "🎎",
    "favorita": "🎵",
    "mais_pedida": "🔥",
    "genero": "🎼",
    "atividade": "📊",
    "insignias": "🏅",
    "importando": "⏳",
    "posicao": "📍",
}
MEDALHAS = ("🥇", "🥈", "🥉")
MESES_EXTENSO = (
    "janeiro",
    "fevereiro",
    "março",
    "abril",
    "maio",
    "junho",
    "julho",
    "agosto",
    "setembro",
    "outubro",
    "novembro",
    "dezembro",
)
COR_ANO = 0xF1C40F

# Limites do Discord para embeds.
LIMITE_TITULO = 256
LIMITE_CAMPO = 1024
LIMITE_DESCRICAO = 4096
LIMITE_EMBED = 6000

_COR_FEEDBACK = {"sucesso": COR_SUCESSO, "aviso": COR_AVISO, "erro": COR_ERRO}


def barra(atual, total, largura=10):
    """Barra de progresso ▰▰▰▱▱: sempre entre vazia e cheia, mesmo com valores fora da faixa."""
    cheias = 0 if total <= 0 else max(0, min(largura, round(largura * atual / total)))
    return "▰" * cheias + "▱" * (largura - cheias)


def rodape(*partes):
    """'MeMi BOT · a · b', ignorando partes vazias."""
    return " · ".join([BOT_NAME] + [p for p in partes if p])


def prefixo_posicao(posicao):
    """Medalha para o top 3; os demais recebem o número em código (`04`)."""
    return MEDALHAS[posicao - 1] if 1 <= posicao <= len(MEDALHAS) else f"`{posicao:02d}`"


def milhar(n):
    """1234 -> '1.234' (jeito brasileiro)."""
    return f"{n:,}".replace(",", ".")


def plural(n, um, varios):
    """'1 pedido' / '2.000 pedidos'."""
    return f"{milhar(n)} {um if n == 1 else varios}"


def linha_faixa(titulo, artista, vezes):
    """'**Título** · Artista · 3x' (cortando nomes muito longos)."""
    return f"**{titulo[:65]}**" + (f" · {artista[:45]}" if artista else "") + f" · {vezes}x"


def rotulo_periodo(tipo, periodo):
    """'2025-12' -> 'dezembro de 2025'; '2025' -> '2025'."""
    if tipo == "ano":
        return periodo
    ano, mes = periodo.split("-")
    return f"{MESES_EXTENSO[int(mes) - 1]} de {ano}"


def cortar(texto, limite, sufixo="…"):
    texto = str(texto)
    return texto if len(texto) <= limite else texto[: limite - len(sufixo)] + sufixo


def campo(embed, nome, valor, inline=True):
    """Adiciona o campo só se tiver conteúdo (campo vazio parece defeito). True se adicionou."""
    valor = str(valor if valor is not None else "").strip()
    if not valor:
        return False
    embed.add_field(
        name=cortar(nome, LIMITE_TITULO), value=cortar(valor, LIMITE_CAMPO), inline=inline
    )
    return True


def feedback(tipo, texto):
    """Embed pequeno e colorido de confirmação ('sucesso'), aviso ('aviso') ou erro ('erro')."""
    return discord.Embed(
        description=cortar(f"{EMOJI[tipo]} {texto}", LIMITE_DESCRICAO), color=_COR_FEEDBACK[tipo]
    )


async def responder(ctx, tipo, texto):
    return await ctx.send(
        embed=feedback(tipo, texto), allowed_mentions=discord.AllowedMentions.none()
    )


def embed_ranking(
    titulo,
    itens,
    *,
    pagina=0,
    por_pagina=10,
    subtitulo="",
    rodape_partes=(),
    capa="",
    meu_indice=None,
    cor=COR_PADRAO,
    numerar=True,
):
    """Uma página de ranking. `itens` são textos prontos ('**Nome** · detalhe'); a posição
    (medalha ou número) é acrescentada aqui. `meu_indice` é a posição (0-based) de quem pediu:
    se ela não estiver na página mostrada, aparece 'Sua posição' no fim."""
    total_paginas = max(1, -(-len(itens) // por_pagina))
    pagina = max(0, min(pagina, total_paginas - 1))
    ini = pagina * por_pagina
    linhas = [
        f"{prefixo_posicao(pos)} {item}" if numerar else item
        for pos, item in enumerate(itens[ini : ini + por_pagina], start=ini + 1)
    ]
    texto = "\n".join(linhas) or "*Ninguém no ranking ainda.*"
    if subtitulo:
        texto = f"*{subtitulo}*\n\n{texto}"
    if meu_indice is not None and not ini <= meu_indice < ini + por_pagina:
        texto += f"\n\n{EMOJI['posicao']} Sua posição: **#{meu_indice + 1}** de {len(itens)}"
    embed = discord.Embed(
        title=cortar(titulo, LIMITE_TITULO), description=cortar(texto, LIMITE_DESCRICAO), color=cor
    )
    if capa:
        embed.set_thumbnail(url=capa)
    embed.set_footer(text=rodape(f"página {pagina + 1}/{total_paginas}", *rodape_partes))
    return embed


def embed_ajuda(secoes):
    """Ajuda agrupada: cada seção é (título, [(comando, descrição), ...])."""
    blocos = [
        f"**{nome}**\n" + "\n".join(f"`{comando}` — {descricao}" for comando, descricao in comandos)
        for nome, comandos in secoes
    ]
    embed = discord.Embed(
        title=f"📖 Comandos do {BOT_NAME}",
        description=cortar("\n\n".join(blocos), LIMITE_DESCRICAO),
        color=COR_PADRAO,
    )
    embed.set_footer(text=rodape("mês e ano atuais", "horário de Brasília"))
    return embed


def _lista_top(linhas, formatar):
    """Linhas com medalha/número: '🥇 texto'."""
    return "\n".join(f"{prefixo_posicao(i)} {formatar(x)}" for i, x in enumerate(linhas, start=1))


def embed_resumo(tipo, periodo, dados, nome, com_imagem=False):
    """Resumo de fechamento de mês ('mes') ou ano ('ano'). `nome` converte ID em nome de exibição.
    Com imagem (que já traz vencedores, música e números): o mês fica só com o título e o ano
    mantém os tops."""
    djs, tagarelas = dados.get("djs", []), dados.get("tagarelas", [])
    musicas, artistas = dados.get("musicas", []), dados.get("artistas", [])
    ano = tipo == "ano"

    def pedidos(x):
        return f"**{nome(x[0])}** · {plural(x[1], 'pedido', 'pedidos')}"

    def mensagens(x):
        return f"**{nome(x[0])}** · {plural(x[1], 'mensagem', 'mensagens')}"

    if ano:
        embed = discord.Embed(title=f"🏁 Resumo de {periodo}", color=COR_ANO)
        base = periodo
        campo(embed, "🏆 Top 3 DJs", _lista_top(djs, pedidos), False)
        campo(embed, "🏆 Top 3 tagarelas", _lista_top(tagarelas, mensagens), False)
        campo(embed, "🎵 Top 5 músicas", _lista_top(musicas, lambda x: linha_faixa(*x)), False)
        campo(
            embed,
            "🎤 Top 5 artistas",
            _lista_top(
                artistas,
                lambda x: f"**{x[0][:80]}** · {plural(x[1], 'música', 'músicas')}"
                f" · {plural(x[2], 'tocada', 'tocadas')}",
            ),
            False,
        )
    else:
        rotulo = rotulo_periodo(tipo, periodo)
        embed = discord.Embed(title=f"📅 Resumo de {rotulo}", color=COR_PADRAO)
        base = rotulo.split(" de ")[0]
        if djs and not com_imagem:
            campo(embed, "🎧 DJ do Mês", pedidos(djs[0]), False)
        if tagarelas and not com_imagem:
            campo(embed, "💬 Tagarela do Mês", mensagens(tagarelas[0]), False)
        if musicas and not com_imagem:
            campo(embed, "🎵 Música do mês", linha_faixa(*musicas[0]), False)
    if not com_imagem:
        numeros = (
            f"{plural(dados.get('mensagens', 0), 'mensagem', 'mensagens')}"
            f" · {plural(dados.get('pedidos', 0), 'pedido', 'pedidos')}"
        )
        campo(embed, "📊 O ano em números" if ano else "📊 O mês em números", numeros, False)
    embed.set_footer(text=rodape(f"fechamento de {base}"))
    return embed


def texto_progresso(nivel, avanco, meta, maximo=100):
    """'▰▰▰▱▱ 120/380 XP' ou 'nível máximo'."""
    if nivel >= maximo:
        return f"{barra(1, 1)} nível máximo"
    return f"{barra(avanco, meta)} {milhar(avanco)}/{milhar(meta)} XP"


def embed_nivel(
    nome,
    nivel,
    progresso,
    patente,
    *,
    trocou=False,
    icone="",
    avatar_url="",
    com_imagem=False,
):
    """Aviso de level up. `progresso` = (nível atual, avanço, meta); `patente` = dict com nome e
    cor. Troca de patente ganha título e cor próprios. Sem imagem, mostra o progresso e o avatar;
    com imagem (que já traz os dois), o embed fica só com o texto."""
    marca = f"{icone} " if icone else ""
    if trocou:
        titulo = f"🎖️ Nova patente: {patente['nome']}"
        texto = f"**{nome}** chegou ao nível **{nivel}** e agora é {marca}**{patente['nome']}**!"
        cor = patente.get("cor") or COR_ANO
    else:
        titulo = f"⬆️ Nível {nivel}"
        texto = f"**{nome}** subiu para o nível **{nivel}** · {marca}{patente['nome']}"
        cor = COR_ANO
    embed = discord.Embed(
        title=cortar(titulo, LIMITE_TITULO),
        description=cortar(texto, LIMITE_DESCRICAO),
        color=cor,
    )
    atual, avanco, meta = progresso
    if not com_imagem:
        campo(embed, f"Nível {atual}", texto_progresso(atual, avanco, meta), False)
        if avatar_url:
            embed.set_thumbnail(url=avatar_url)
    embed.set_footer(text=rodape("mm!levels"))
    return embed


SECOES_CHANGELOG = {
    "novidades": "✨ Novidades",
    "melhorias": "🔧 Melhorias",
    "correcoes": "🐛 Correções",
    "admin": "⚙️ Para quem administra",
}


def _campos_lista(embed, nome, itens):
    """Lista em tópicos; se passar de 1024 caracteres, continua em campos '(cont.)'."""
    bloco, tamanho, primeiro = [], 0, True
    for item in itens + [None]:
        linha = None if item is None else "• " + cortar(item, LIMITE_CAMPO - 2)
        if bloco and (linha is None or tamanho + 1 + len(linha) > LIMITE_CAMPO):
            embed.add_field(
                name=nome if primeiro else f"{nome} (cont.)", value="\n".join(bloco), inline=False
            )
            bloco, tamanho, primeiro = [], 0, False
        if linha is not None:
            bloco.append(linha)
            tamanho += len(linha) + (1 if len(bloco) > 1 else 0)


def embed_changelog(entrada, indice, total):
    """Uma versão do histórico: título, data e as seções (novidades, melhorias, correções, admin)."""
    descricao = f"**{entrada['titulo']}**"
    if entrada.get("data"):
        descricao = f"*{entrada['data']}* · " + descricao
    embed = discord.Embed(
        title=f"📋 {BOT_NAME} · versão {entrada['versao']}",
        description=cortar(descricao, LIMITE_DESCRICAO),
        color=COR_PADRAO,
    )
    for chave, nome in SECOES_CHANGELOG.items():
        if entrada.get(chave):
            _campos_lista(embed, nome, list(entrada[chave]))
    partes = [f"versão {indice + 1} de {total}"]
    if total > 1:
        partes.append("use o menu para ver outras")
    embed.set_footer(text=rodape(*partes))
    return embed
