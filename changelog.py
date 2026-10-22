"""Histórico de versões do MeMi BOT, mostrado pelo comando mm!changelog.

Para publicar uma versão nova, acrescente um item NO TOPO de VERSOES (o mais novo primeiro).
Cada seção é uma lista de frases curtas, escritas para quem usa o bot. Seções vazias são omitidas.
"""

# Ordem em que as seções aparecem no embed.
SECOES = ("novidades", "melhorias", "correcoes", "admin")

VERSOES = [
    {
        "versao": "3.4.0",
        "data": "22/10/2026",
        "titulo": "FUT.AE — update visual, missões e temporada",
        "novidades": [
            "Cartas com moldura própria para cada raridade, do Base à Única (supernova). Lendas "
            "ganham uma etiqueta discreta na borda da foto.",
            "Crates abrem em dois tempos: o pacote treme e depois revela a raridade com animação.",
            "Placar final em imagem depois do amistoso e da final do campeonato.",
            "`mm!time` mostra o cartão do clube (cosméticos, forma, títulos, artilheiro); um "
            "botão alterna para a escalação.",
            "`mm!construir` mostra o prédio do Centro de Treinamento em imagem.",
            "`mm!cosmeticos` virou galeria: um item por página, com botão para equipar.",
            "`mm!missoes`: 3 missões por dia e 2 por semana que pagam Memilets.",
            "`mm!temporada`: tabela do mês somando todos os campeonatos diários.",
            "`mm!rt`: zera o claim na hora, a cada 12 h, liberado pelo upgrade Renovação de "
            "Contrato.",
            "Chequer (CA, Cheréca FC) entrou na categoria Cartola.",
        ],
        "melhorias": [
            "Garantia nas crates: a cada 40 caixas sem Raro, a seguinte traz um.",
            "Ícones dos upgrades e medalhões redesenhados em estilo minimalista.",
            "Técnico sem foto aparece com o medalhão de técnico na escalação.",
        ],
    },
    {
        "versao": "3.3.0",
        "data": "22/10/2026",
        "titulo": "FUT.AE V2 — loja, crates, Centro de Treinamento e cosméticos",
        "novidades": [
            "`mm!construir`: Centro de Treinamento, uma torre de 12 andares com upgrades "
            "eternos (wishes, Star Wish, daily mais rápido, bilheteria, bolas…). `mm!destruir` "
            "devolve todos os Memilets gastos.",
            "Cosméticos: emblema, uniforme, estádio, técnico, liga e mascote saem nas crates e "
            "só dá para equipar o que é seu (`mm!cosmeticos`, `mm!emblema`, `mm!tecnico`…). "
            "Cada um equipado soma +100 ao máximo do daily.",
            "Estádio e `mm!renda`: o primeiro amistoso do dia em casa gera renda (gols e "
            "vitória aumentam, derrota diminui). O mandante confirma antes.",
            "`mm!bilheteria`: com o andar 5, cada jogo do campeonato guarda Memilets para você.",
            "O campeão do campeonato diário ganha 500 Memilets.",
            "73 lendas novas dos 12 grandes clubes brasileiros (overall 83 a 89), sem repetir "
            "quem já estava no jogo.",
            "Moeda Memilets com emoji próprio (um gato de óculos) e ícones redondos coloridos "
            "para cada upgrade.",
        ],
        "melhorias": [
            "`mm!upgrade` refeito: 10 upgrades com limite, Star Wish comprável e confirmação "
            "antes de cada compra. Nenhum upgrade aumenta a sorte.",
            "`mm!crates` refeito: toda caixa traz um item Básico e, com sorte, um de outra "
            "raridade (Incomum, Raro, Épico ou Lendário); 5% de chance de o item vir em dobro.",
            "Rolls e claims renovam a cada 30 minutos.",
            "Daily agora paga de 100 a 300 Memilets, mais os bônus de cosméticos e upgrades.",
            "Clubes e logos viraram **emblemas** no `mm!scout`, `mm!top` e `mm!left`.",
            "A escalação mostra o emblema embaixo à esquerda e o técnico embaixo à direita.",
            "Os menus de upgrades, crates, torre, renda e cosméticos explicam o sistema no topo.",
        ],
        "correcoes": [
            "Uniformes 2 e 3 de um mesmo time não sumiam mais atrás do uniforme principal.",
        ],
        "admin": [
            "O bônus de duplicata foi removido e devolvido em Memilets; níveis acima dos "
            "limites novos também foram devolvidos.",
            "Quem já tinha técnico, estádio, liga, mascote ou emblema no time passou a possuir "
            "esse item.",
        ],
    },
    {
        "versao": "3.2.0",
        "data": "21/10/2026",
        "titulo": "FUT.AE — enciclopédia, cartas estilo Mudae e roleta maior",
        "novidades": [
            "Enciclopédia do Futebol: 500 lendas com overall de 90 a 99, além de 100 técnicos, "
            "100 estádios, 100 ligas e 100 mascotes no catálogo e no `mm!scout`.",
            "`mm!elenco` mostra uma carta por página, com foto grande, overall e posição, como "
            "o `$mm` do Mudae. O botão **Lista** mostra o elenco inteiro.",
            "`mm!scout` e `mm!roll` usam a mesma carta e mostram quem já contratou o jogador.",
            "`mm!time liga <nome>` escolhe a liga favorita do time.",
        ],
        "melhorias": [
            "Cerca de 31 mil jogadores que estavam fora da roleta agora podem sair no roll.",
            "Rolls renovam a cada 1 hora, na hora cheia. Todos os horários usam Brasília.",
            "Comandos usam o nome do jogador: `mm!wish kvara`, `mm!tatica capitao kvara`, "
            "`mm!troca @pessoa kvara por vini`. O ID só aparece quando há homônimos.",
            "`mm!wish` com o nome exato escolhe direto; com vários resultados, abre um menu.",
            "A wishlist mostra no topo os slots usados de wish e de Star Wish.",
            "Nomes de times do Cartola corrigidos (acentos, espaços) e logos da pasta viram "
            "clubes no banco.",
        ],
        "correcoes": [
            "`mm!tatica capitao/falta/penalti` não funcionava sem ID.",
            "`mm!wish` dava erro quando os slots de wish estavam cheios.",
        ],
        "admin": [
            "A CI agora roda os testes do FUT.AE e confere a formatação dele.",
            "Dependências travadas num único `requirements.txt`.",
        ],
    },
    {
        "versao": "3.1.0",
        "data": "20/10/2026",
        "titulo": "FUT.AE — escalação clicável, jogos narrados e overall novo",
        "novidades": [
            "`mm!escalar` abre um montador: escolha a formação, clique na posição e escolha o "
            "jogador (só os daquela posição, por overall). Tem também **Escalar automático**.",
            "`mm!time` mostra a imagem da formação escolhida, com fotos, overall e vagas livres.",
            "`mm!amistoso` agora é narrado minuto a minuto: gols, defesas, cartões, intervalo, "
            "acréscimos e segundo tempo. Final de campeonato empatada vai para os pênaltis.",
            "`mm!tu` mostra rolls restantes, claim e daily com horário, como no Mudae.",
            "`mm!scout` mostra a foto do jogador e também procura técnicos, estádios, clubes, "
            "ligas, uniformes, emblemas, logos e mascotes.",
            "`mm!left` mostra quantos jogadores de cada série ainda estão livres (livres/total).",
        ],
        "melhorias": [
            "Overall reformulado: notas baixas foram valorizadas, jogadores do Cartola são 90 "
            "e Maria Sofia é a única carta 100 (com raridade própria no roll).",
            "Crates melhores: Memilets nunca voltam menos de 350, jackpot de até 5.000, reset "
            "de claim, mais rolls extras e garantia de prêmio permanente a cada 50.",
            "`mm!carteira` virou embed com saldo, rolls, wishes, timers e garantia das crates.",
            "Comandos aceitam qualquer maiúscula no prefixo (MM!, Mm!, mM!), espaço depois "
            "dele e acentos (`mm!tática`, `mm!escalação`).",
            "Fotos já baixadas aparecem no roll, no scout e na escalação mesmo sem estarem "
            "ligadas ao banco.",
            "`mm!top cartola`, `mascotes`, `uniformes` e `emblemas` mostram nomes padronizados "
            "(só o time, ou Time (Mascote)).",
            "`mm!elenco` e `mm!top` paginados e ordenados por overall.",
        ],
        "correcoes": [
            "O bot voltava a falhar ao iniciar por causa de um `mm!help` duplicado.",
            "`mm!upgrade` dava erro ao listar o bônus de duplicata.",
        ],
        "admin": [
            "`mm!testeamistoso` e `mm!testecampeonato` funcionam direto, com narração e pênaltis.",
            "`mm!admin duplicados` encontra jogadores repetidos e remove o de menor overall, "
            "levando posse, bolas e wishes para o que fica. Também via `python -m src.dedupe`.",
        ],
    },
    {
        "versao": "3.0.0",
        "data": "19/10/2026",
        "titulo": "FUT.AE — coleção e simulação de futebol",
        "novidades": [
            "Novo módulo FUT.AE para coleção de jogadores, rolagens, wishes, Star Wishes, "
            "economia, times, amistosos e campeonatos.",
            "Catálogo com `mm!top` e categorias de jogadores, técnicos, uniformes, mascotes, "
            "estádios, ligas, Cartola e emblemas, com paginação.",
            "`mm!scout` identifica jogadores por nome, overall, posição, série e ID quando "
            "existem homônimos.",
            "`mm!wishlist`/`mm!wl` mostra todas as wishes em embed; Star Wishes aparecem com "
            "estrela. `mm!starwish` também aceita `mm!sw`.",
            "Comandos administrativos de teste para amistosos e campeonatos fictícios, "
            "restritos ao proprietário do bot.",
        ],
        "melhorias": [
            "`mm!daily` usa cooldown de 24 horas e mostra o valor recebido e o máximo possível.",
            "Trocas exigem confirmação dos dois lados e demissões exigem confirmação por botão.",
            "Upgrades incluem bônus progressivo para recompensas de duplicatas.",
            "A carta especial Maria Sofia usa overall 100; Messi e Cristiano Ronaldo estão "
            "com overall 99.",
            "`mm!ajuda` continua sendo o menu geral do MeMi BOT; `mm!fut.ae` e `mm!futae` "
            "ficam reservados ao menu do módulo FUT.AE.",
            "O bot aguarda locks temporários do SQLite para funcionar junto com o coletor "
            "de fotos sem falhar imediatamente.",
        ],
        "admin": [
            "O banco modular usa schema v11 e migra automaticamente sem apagar os dados.",
            "Fotos e assets locais ficam fora do Git; o repositório contém apenas código, "
            "testes e documentação.",
        ],
    },
    {
        "versao": "2.2.0",
        "data": "30/09/2026",
        "titulo": "Patentes, tags, Mudae tracker e imagens maiores",
        "novidades": [
            "Níveis de 1 a 100 com XP: 1 por mensagem, 25 por música pedida e ½ por roletada. "
            "Subir fica mais difícil a cada nível.",
            "Patentes a cada 10 níveis, de Figurante a Demiurgo Supremo (nível 100), com emblema "
            "próprio. A patente também vira título.",
            "Tags: títulos e insígnias viraram uma coisa só, com as insígnias novas como emojis "
            "personalizados. `mm!tags [@pessoa]` lista as suas; `mm!tags todos` mostra todas e "
            "como ganhar.",
            "Mudae tracker: `mm!mudae` mostra o panorama do servidor, `mm!mudae @pessoa` os "
            "números de alguém e `mm!mudae personagem NOME` tudo sobre um personagem.",
            "Rankings do Mudae: roletadores, casamentos, kakera, personagens, séries, snipers e "
            "azarados (`mm!mudae casamentos mes`, por exemplo).",
            "Tags novas do Cartola e da comunidade: Premier League IOS, Copa IOS, Pintos Corridos, "
            "Cartoleiro de Ouro, IOS World Cup, IOS Club WC e Papagaio da Call.",
            "Atalhos: `mm!tg`, `mm!lvl`, `mm!md`, `mm!h`, `mm!msc`, `mm!w`, `mm!p`, `mm!t`, "
            "`mm!th`, `mm!f`, `mm!fm`, `mm!c`, `mm!cl` e `mm!embedcolor`.",
        ],
        "melhorias": [
            "Todo nível é anunciado; múltiplos de 5 e troca de patente saem com imagem.",
            "Imagens com textos bem maiores. O cartão usa o fundo novo, com seu avatar na lente, "
            "e sai maior no chat.",
            "Perfil: o título aparece com a insígnia; a página 2 mostra as tags por categoria e "
            "como você ganhou cada uma.",
            "`mm!hall` em blocos por período, com nomes longos cortados.",
            "`mm!tagarelas` mostra só pessoas; use `bots` para ver os bots.",
            "`mm!musicas genero` mostra só o ranking de gêneros.",
            "Resumo do mês e do ano com uma seção do Mudae.",
            "Ajuda com o banner novo e as categorias Mudae e Personalização.",
        ],
        "admin": [
            "Atualizar: `git pull` e `python -m pip install -r requirements.txt`. O banco migra "
            "sozinho para a versão 4, com backup em `backups/migracao_*`.",
            "Os níveis são recalculados na nova escala sem avisos retroativos. Títulos e "
            "insígnias antigos viram tags, sem perder nada.",
            "Ao ligar, o bot envia as insígnias como emojis da aplicação: não precisa subir "
            "emoji no servidor. Imagens novas em `assets/insignias` são enviadas sozinhas.",
            "Na primeira vez, o bot relê em segundo plano os canais do Mudae para montar o "
            "histórico. O rodapé do `mm!mudae` avisa enquanto isso.",
            "`mm!give @pessoa TAG` concede tags manuais. `mm!mudaedump [#canal]` exporta mensagens "
            "do Mudae para conferir a leitura.",
        ],
    },
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
