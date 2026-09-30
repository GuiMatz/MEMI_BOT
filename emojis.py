"""Emojis personalizados do MeMi BOT, enviados como emojis da aplicação (bot).

Emojis da aplicação funcionam em qualquer servidor, não ocupam espaço no servidor e não exigem
permissão nenhuma: o próprio bot os envia ao ligar. Fontes:

- insígnias das tags: assets/insignias/<imagem> (ver tags.py);
- patentes: assets/patentes/<id>.png, ou um emblema gerado pelo bot se o arquivo não existir;
- emojis do bot: assets/emojis/<chave>.png substitui estilo.EMOJI[<chave>] (ex.: musica.png).

Cada emoji guarda no banco uma assinatura da imagem: só é reenviado quando o arquivo muda. Os
emojis com o prefixo "mm_" que saíram do catálogo são apagados; os outros não são tocados.
Qualquer falha (rede, limite, imagem inválida) só é registrada no log: o bot segue com os emojis
padrão do Discord.
"""

import hashlib
import logging
from pathlib import Path

import discord

import estilo
import imagens
import progressao
import tags

PREFIXO = "mm_"
PREFIXO_ESTILO = "mm_e_"
PASTA_EMOJIS = Path(__file__).with_name("assets") / "emojis"
PASTA_PATENTES = Path(__file__).with_name("assets") / "patentes"


def nome_emoji(chave):
    """Nome válido para o Discord (2 a 32 caracteres: letras, números e _)."""
    return (PREFIXO + chave)[:32]


def itens_para_enviar():
    """{nome_do_emoji: bytes da imagem} de tudo que deve existir como emoji da aplicação."""
    itens = {}
    for ident, info in tags.CATALOGO.items():
        if info.get("imagem"):
            caminho = tags.PASTA_INSIGNIAS / info["imagem"]
            if caminho.exists():
                itens[nome_emoji(ident)] = caminho.read_bytes()
    for item in progressao.PATENTES:
        caminho = PASTA_PATENTES / f"{item['id']}.png"
        if caminho.exists():
            itens[nome_emoji(tags.id_patente(item))] = caminho.read_bytes()
        elif imagens.disponivel():
            try:
                itens[nome_emoji(tags.id_patente(item))] = imagens.gerar_emblema(item)
            except Exception:  # noqa: BLE001 - sem emblema, fica o emoji padrão
                logging.exception("Falha ao gerar o emblema da patente %s", item["id"])
    if PASTA_EMOJIS.is_dir():
        for arquivo in sorted(PASTA_EMOJIS.glob("*.png")):
            if arquivo.stem in estilo.EMOJI:
                itens[(PREFIXO_ESTILO + arquivo.stem)[:32]] = arquivo.read_bytes()
    return itens


async def sincronizar(bot, banco, itens):
    """Garante que cada item exista como emoji da aplicação. Devolve {nome: '<:nome:id>'}."""
    try:
        existentes = {e.name: e for e in await bot.fetch_application_emojis()}
    except (discord.HTTPException, OSError) as erro:
        logging.warning("Não consegui listar os emojis da aplicação (%s); uso os padrão.", erro)
        return {}
    feitos = {}
    for nome, dados in itens.items():
        assinatura = hashlib.sha1(dados).hexdigest()[:16]
        chave = f"emoji:{nome}"
        atual = existentes.get(nome)
        if atual is not None and banco.estado(chave) == assinatura:
            feitos[nome] = str(atual)
            continue
        try:
            if atual is not None:
                await atual.delete()
                atual = None
            novo = await bot.create_application_emoji(name=nome, image=dados)
        except (discord.HTTPException, OSError) as erro:
            logging.warning("Não consegui enviar o emoji %s (%s).", nome, erro)
            if atual is not None:
                feitos[nome] = str(atual)
            continue
        banco.definir_estado(chave, assinatura)
        feitos[nome] = str(novo)
    for nome, emoji in existentes.items():
        if nome.startswith(PREFIXO) and nome not in itens:
            try:
                await emoji.delete()
            except (discord.HTTPException, OSError) as erro:
                logging.warning("Não consegui apagar o emoji antigo %s (%s).", nome, erro)
    return feitos


def aplicar(feitos):
    """Passa a usar os emojis enviados: nas tags (tags.ICONES) e no estilo (estilo.EMOJI)."""
    for ident in tags.CATALOGO:
        texto = feitos.get(nome_emoji(ident))
        if texto:
            tags.ICONES[ident] = texto
    for chave in list(estilo.EMOJI):
        texto = feitos.get((PREFIXO_ESTILO + chave)[:32])
        if texto:
            estilo.EMOJI[chave] = texto
