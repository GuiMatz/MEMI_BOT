"""Histórico de versões do MeMi BOT, mostrado pelo comando mm!changelog.

Para publicar uma versão nova, acrescente um item NO TOPO de VERSOES (o mais novo primeiro).
Cada seção é uma lista de frases curtas, escritas para quem usa o bot. Seções vazias são omitidas.
"""

# Ordem em que as seções aparecem no embed.
SECOES = ("novidades", "melhorias", "correcoes", "admin")

VERSOES = [
    {
        "versao": "2.1.0",
        "data": "30/09/2026",
        "titulo": "Imagens, resumos automáticos e visual novo",
        "novidades": [
            "Imagens geradas pelo bot: banner na ajuda, cartão de perfil (`mm!cartao`), aviso de "
            "level up, resumo do mês/ano e wrapped, todas no mesmo visual.",
            "Resumo automático no canal de avisos quando um mês ou ano fecha: DJ, Tagarela, música "
            "mais tocada e números.",
            "`mm!hall [mes/ano]`: os vencedores dos meses e anos anteriores.",
            "Aviso de level up a cada 10 níveis (10, 20, 30…).",
            "`mm!ec COR`: escolha a cor do seu perfil e das suas imagens.",
            "`mm!changelog`: esta lista, com as versões anteriores.",
        ],
        "melhorias": [
            "Mensagens redesenhadas: rankings com medalhas e “Sua posição”, perfil com barra de "
            "nível e sem campos vazios, ajuda em categorias, confirmações e erros coloridos.",
            "Rankings mostram só o nome em negrito (sem menção) e não listam quem saiu do servidor.",
            "Frase do perfil em itálico; a página de títulos avisa quando não há títulos ou insígnias.",
            "`mm!musicas genero` virou um flag do `mm!musicas`; rankings semanais saíram (só mês e ano).",
            "Leitura de segurança mais leve e backup diário que não trava o bot.",
        ],
        "correcoes": [
            "Um chat sem permissão de histórico não trava mais a importação e as conquistas.",
            "Uma falha de leitura não repete mais a sincronização completa a cada 15 segundos.",
            "Erros de rede não perdem mais avisos e resumos.",
            "Avisos de conquista com acentos corretos; frases de várias linhas não quebram o itálico.",
        ],
        "admin": [
            "Atualizar: `git pull` e `python -m pip install -r requirements.txt` (agora inclui o "
            "Pillow, das imagens). O banco migra sozinho, com backup em `backups/migracao_*`.",
            "Não há configuração nova. Sem Pillow, ou se uma imagem falhar, a mensagem sai só em texto.",
            "Ative a Server Members Intent no Developer Portal para esconder quem saiu; sem ela o "
            "bot funciona igual e registra um aviso no log.",
            "`mm!exportar` agora é só do dono. O bot não abre duas vezes por engano.",
        ],
    },
    {
        "versao": "2.0.0",
        "data": "",
        "titulo": "MeMi BOT 2 (versão base)",
        "novidades": [
            "Rankings de músicas, artistas, gêneros, mensagens, níveis e roletadas do Mudae.",
            "Mensagens em tempo real, recuperação depois de quedas e importação do histórico.",
            "Níveis de 1 a 1000, títulos, insígnias e Top 1 rotativo, com fechamento de mês e ano.",
            "Perfil em três páginas, `mm!wrapped`, `mm!aleatoria`, frase e música favorita.",
            "Permissões por ID do dono e backup diário do banco.",
        ],
    },
]

VERSAO_ATUAL = VERSOES[0]["versao"]


def buscar(texto):
    """Posição em VERSOES de um texto como '2.1.0', 'v2.1' ou '2.0' (prefixo único), ou None."""
    texto = str(texto or "").strip().lower().removeprefix("v")
    if not texto:
        return None
    for i, entrada in enumerate(VERSOES):
        if entrada["versao"] == texto:
            return i
    candidatas = [i for i, e in enumerate(VERSOES) if e["versao"].startswith(texto + ".")]
    return candidatas[0] if len(candidatas) == 1 else None
