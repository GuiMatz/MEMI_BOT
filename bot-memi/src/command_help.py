"""`mm!ajuda <comando>`: explicação e exemplos de cada comando do FUT.AE.

A lista geral (`mm!ajuda` sem nada) continua nas páginas de cada bot; aqui fica o detalhe.
Aceita o nome ou qualquer alias (`mm!ajuda sw` mostra o `mm!starwish`).
"""

from __future__ import annotations

import discord

# Comando -> (uso, explicação, exemplos).
COMMAND_HELP: dict[str, tuple[str, str, tuple[str, ...]]] = {
    "roll": (
        "mm!roll",
        "Sorteia um jogador. Livre: o botão **Reivindicar** contrata (1 por ciclo de 30 min, "
        "mais as contratações extras). Seu: ganha uma bola. De outra pessoa: Memilets. O botão "
        "**Rolar de novo** rola sem digitar.",
        ("mm!roll",),
    ),
    "daily": (
        "mm!daily",
        "Memilets uma vez por dia (mais com cosméticos, upgrades e torre). A resposta mostra as "
        "missões do dia e o botão para resgatar as prontas.",
        ("mm!daily",),
    ),
    "tu": (
        "mm!tu",
        "Timers da conta (rolls, claim, daily) com atalhos: Rolar, Daily, Missões, Ativar "
        "rolls e Inventário.",
        ("mm!tu",),
    ),
    "carteira": (
        "mm!carteira",
        "Saldo, elenco, rolls (do ciclo, ativos e guardados), wishes e timers, com os mesmos "
        "atalhos do `mm!tu`.",
        ("mm!carteira",),
    ),
    "shop": (
        "mm!shop",
        "A loja inteira: caixas, pacotes, all-in, upgrades e torre, com botões de compra. "
        "Botão cinza = falta saldo (mostra quanto).",
        ("mm!shop", "mm!loja"),
    ),
    "crates": (
        "mm!crates [epica] [1|5|10]",
        "Caixas com rolls guardados, cosméticos, bônus, caixas e pacotes. Garantia: Raro em 15 "
        "caixas sem Raro, Épico em 50 sem Épico. A caixa épica nunca vem só com o básico.",
        ("mm!crates", "mm!crates 10", "mm!crates epica 5"),
    ),
    "pacote": (
        "mm!pacote [epico]",
        "Uma carta livre do mercado (nunca Base). A do pacote comum vende na hora por 50%; a do "
        "épico, pelo valor cheio. As duas ficam 1 hora sem poder ser doadas ou trocadas.",
        ("mm!pacote", "mm!pacote epico"),
    ),
    "allin": (
        "mm!allin",
        "Aposta: uma roleta com 1 chance em 10 de Ícone ou melhor; nas outras fatias, uma "
        "carta comum. Sem Ícone livre no mercado, não gira e não cobra.",
        ("mm!allin",),
    ),
    "inventario": (
        "mm!inventario",
        "Caixas e pacotes ganhos nas caixas. **Abrir** abre um (de graça); **Abrir todas as "
        "caixas** abre até 20 num lote.",
        ("mm!inventario", "mm!inv"),
    ),
    "ativar": (
        "mm!ativar <quantidade>",
        "Ativa rolls guardados das caixas: valem depois dos rolls do ciclo, e cada 10 ativados "
        "dão +1 contratação extra (a sobra conta para a próxima).",
        ("mm!ativar 10", "mm!ativar 25"),
    ),
    "upgrade": (
        "mm!upgrade [nome]",
        "Upgrades permanentes (rolls, wishes, Star Wish, daily…). Sempre pede confirmação.",
        ("mm!upgrade", "mm!upgrade olheiro"),
    ),
    "construir": (
        "mm!construir [1-12]",
        "A torre do Centro de Treinamento: menu para escolher o andar e botão de destruir "
        "(devolve tudo).",
        ("mm!construir", "mm!construir 7", "mm!destruir"),
    ),
    "wish": (
        "mm!wish [jogador]",
        "Marca um jogador que você quer: ele aparece mais protegido para você quando alguém "
        "rola. Sem nome, mostra suas wishes. Também como `/wish`, com sugestões.",
        ("mm!wish Vini Jr", "mm!wl"),
    ),
    "starwish": (
        "mm!starwish [jogador]",
        "Wish especial: aumenta a chance de o jogador aparecer nos seus rolls.",
        ("mm!sw Haaland",),
    ),
    "vender": (
        "mm!vender [jogador | filtro]",
        "Vende jogadores. Sem nada, abre o painel. Filtros vendem vários de uma vez (sem "
        "titulares nem protegidos), sempre com o total antes.",
        ("mm!vender Fulano", "mm!vender base", "mm!vender abaixo 70", "mm!vender posicao ZAG"),
    ),
    "proteger": (
        "mm!proteger [jogador]",
        "Liga/desliga o 🔒: o jogador some de todas as vendas.",
        ("mm!proteger Fulano",),
    ),
    "dar": (
        "mm!dar @pessoa <jogador | valor>",
        "Doa um jogador ou Memilets, com confirmação. A outra pessoa não precisa aceitar.",
        ("mm!dar @Ana Fulano", "mm!dar @Ana 5000"),
    ),
    "pay": (
        "mm!pay @pessoa <quantia>",
        "Manda Memilets para outra pessoa, com confirmação.",
        ("mm!pay @Ana 5000", "mm!pagar @Ana 1.000"),
    ),
    "troca": (
        "mm!troca @pessoa",
        "Abre uma proposta de troca de jogadores e Memilets; os dois confirmam.",
        ("mm!troca @Ana",),
    ),
    "elenco": (
        "mm!elenco [@pessoa]",
        "O elenco em cartas, com menu para ordenar.",
        ("mm!elenco", "mm!elenco @Ana"),
    ),
    "scout": (
        "mm!scout <jogador>",
        "A carta e os dados de um jogador. Também como `/scout`, com sugestões.",
        ("mm!scout Messi", "mm!im Messi"),
    ),
    "missoes": (
        "mm!missoes",
        "Missões do dia e da semana, com o botão para resgatar as prontas.",
        ("mm!missoes",),
    ),
    "time": (
        "mm!time",
        "Seu time: escalação, tática e personalização num menu só.",
        ("mm!time",),
    ),
    "amistoso": (
        "mm!amistoso @pessoa",
        "Um amistoso contra outro time; o primeiro do dia em casa gera renda.",
        ("mm!amistoso @Ana",),
    ),
}


def command_help_embed(bot, name: str) -> discord.Embed | None:
    """Embed com o uso e os exemplos de `name` (nome ou alias); None se não existe."""
    command = bot.get_command(name.strip().lower().removeprefix("mm!"))
    if command is None:
        return None
    usage, text, examples = COMMAND_HELP.get(
        command.name, (f"mm!{command.name}", command.help or "Sem descrição ainda.", ())
    )
    embed = discord.Embed(
        title=f"❔ mm!{command.name}", description=text, color=discord.Color.blurple()
    )
    embed.add_field(name="Uso", value=f"`{usage}`", inline=False)
    if examples:
        embed.add_field(
            name="Exemplos", value="\n".join(f"`{example}`" for example in examples), inline=False
        )
    if command.aliases:
        embed.add_field(
            name="Também funciona como",
            value=" · ".join(f"`mm!{alias}`" for alias in command.aliases),
            inline=False,
        )
    return embed
