# Bot Memi — guia de ideias do jogo

Este documento organiza a visão de produto e as regras propostas para a fase de
jogo do Bot Memi. Ele complementa o [documento técnico do projeto](./projeto.md):
os sistemas descritos aqui são ideias e requisitos de produto, não uma afirmação
de que os comandos já foram implementados.

## Visão do jogo

Bot de coleção e gerenciamento de futebol no Discord, com prefixo `mm!` e
Memilets como moeda. Cada personagem ou item tem um único dono. A pessoa rola,
reivindica jogadores, monta um time de 11 titulares e participa de uma liga
diária simulada.

Categorias planejadas:

- Jogadores ativos;
- técnicos, times, mascotes, campeonatos e seleções;
- jogadores aposentados e ícones;
- estádios e uniformes.

Jogadores formam o pool principal. Itens e personagens auxiliares devem ser mais
raros que jogadores comuns e não devem competir com a raridade dos craques.

## Rolagens, raridade e claims

`mm!roll` oferece 10 rolls por ciclo de 30 minutos, renovados nos minutos `:10`
e `:40` de cada hora (UTC).

Em vez de sortear uniformemente do pool, a roleta escolhe primeiro uma faixa de
overall pelas probabilidades abaixo e depois um jogador ativo elegível daquela
faixa. Jogadores já possuídos também podem sair: duplicatas próprias dão bolas
e duplicatas de terceiros dão uma oferta de Memilets. Se uma faixa não tiver
jogadores, suas probabilidades são redistribuídas proporcionalmente entre as
faixas disponíveis.

| Overall | Chance por roll de jogador |
| --- | ---: |
| 1–59 | 51,85% |
| 60–69 | 30% |
| 70–79 | 15% |
| 80–86 | 3% |
| 87–89 | 0,100% |
| 90–94 | 0,040% |
| 95–97 | 0,008% |
| 98–99 | 0,002% |

A soma das faixas 87+ é 0,15% por roll. Dentro da faixa escolhida, todos os
jogadores ativos elegíveis têm a mesma chance. A chance de itens por roll ainda
precisa ser definida; 5% é apenas uma sugestão inicial.

Claims:

- Quem rolou tem cinco segundos de exclusividade, aumentáveis em um segundo
  por nível até 15 segundos. Uma pessoa com o jogador em wish ou Star Wish pode
  disputar durante essa janela.
- A oferta expira em cinco minutos. Depois da janela exclusiva, qualquer pessoa
  pode tentar reivindicar o jogador.
- Cada pessoa tem um intervalo fixo de uma hora entre claims bem-sucedidos;
  não há upgrade de redução do cooldown. A janela exclusiva não reduz o tempo
  de claim.

Claims são transacionais: tentativas simultâneas não podem atribuir o mesmo
jogador a mais de uma pessoa, e o cooldown só é consumido por um claim bem-
sucedido. O reset de cooldown de claim foi removido das crates.

## Posse, duplicatas e trocas

- Cada jogador pertence a uma pessoa por vez.
- Rolar novamente um jogador que já pertence à própria pessoa concede uma bola.
- Cada bola acrescenta um ponto de overall e 100 Memilets, até dez pontos de
  bônus e overall máximo 99.
- Bolas acima do limite convertem-se em 500 Memilets.
- `mm!demitir <id>` libera o jogador e remove suas bolas. O reembolso é 0,05%
  do valor de mercado, usando o valor do banco ou uma estimativa pela fórmula
  descrita abaixo.
- `mm!troca @pessoa <seu_id> <id_dela> [seus_memilets] [memilets_dela]`
  oferece dois jogadores e quantias opcionais de Memilets; só a pessoa
  convidada pode confirmar, e a troca é atômica. Itens ficam fora até existir
  um sistema de itens.

O limite de claims é a proteção principal contra o ciclo de reivindicar e
demitir para obter reembolsos.

## Wishes

Cada conta começa com cinco slots de wish e um Star Wish. Wish normal não
aumenta a chance de rolagem; permite disputar um claim durante a janela
exclusiva. Cada Star Wish aumenta em 25% o peso relativo de cada jogador
marcado na faixa sorteada, sem adicionar 25 pontos percentuais à chance total.
O número de slots normais pode subir em até 20 níveis compráveis; até quatro
slots extras de Star Wish podem sair das crates.

Use `mm!wish <nome>` e `mm!starwish <nome>` para adicionar ou remover uma
marcação; sem argumento, o comando lista as marcações.

## Economia e upgrades

### Daily

`mm!daily` paga aleatoriamente entre 200 e 300 Memilets e pode ser resgatado
uma vez por dia civil em `America/Sao_Paulo` (renova à meia-noite local). Cada
um dos 17 níveis de daily aumenta o teto em 100, até 2.000; o mínimo continua
200.

### Upgrades

| Upgrade | Efeito | Custo proposto |
| --- | --- | --- |
| Roll permanente | +1 roll por ciclo, até 90 níveis | 5k no primeiro, +5k por nível |
| Wish permanente | +1 slot normal, até 20 níveis | 3k no primeiro, +3k por nível |
| Daily | +100 no teto, até 2.000 | 5k por nível (17 níveis) |
| Exclusividade | +1 segundo, até 15 segundos (10 níveis) | 5k no primeiro, +5k por nível |
| Star Wish | +1 slot, até 4 extras | Somente crates |

Não há limite de slots para o elenco. Redução de cooldown de claim e carta de
item foram retiradas do escopo para manter a disputa justa e não antecipar um
sistema de itens inexistente.

### Crates

`mm!crates` abre uma caixa por 500 Memilets:

| Prêmio | Chance |
| --- | ---: |
| 200–450 Memilets | 74% |
| Rolls extras imediatos (+1 a +5) | 25% |
| +1 wish permanente | 0,5% |
| +1 slot permanente de wish | 0,3% |
| +1 roll permanente | 0,2% |

O reset de cooldown foi retirado: sua chance foi somada à recompensa de
Memilets. Como proteção contra azar, a centésima caixa consecutiva sem prêmio
permanente garante um, sorteado com pesos 50% Star Wish, 30% wish e 20% roll
entre as opções ainda abaixo do limite. O contador é persistente por pessoa.

### Valor de mercado e reembolso

Quando o jogador não tem valor de mercado no banco, a estimativa para exibição
e reembolso usa overall 1 = €5 mil e overall 99 = €100 milhões:

```text
5.000 × 20.000 ^ ((overall − 1) / 98)
```

`mm!demitir` devolve 0,05% do valor de mercado em Memilets (`valor × 0,0005`),
arredondado para o inteiro mais próximo e com mínimo de 1 Memilet.

## Time, elenco e tática

Comandos implementados:

- `mm!time`: perfil e personalização do time, incluindo nome, cor, logo,
  mascote, técnico e estádio.
- `mm!elenco [@pessoa] [filtro]`: mostra os IDs dos jogadores; filtros aceitos:
  `posicao CA`, `overall >= 80`, `clube nome` ou `liga nome`.
- `mm!escalar <id>`: escala o jogador em uma vaga compatível; `mm!escalar
  remover <id>` tira da escalação.
- `mm!tatica formacao <formação>` e `mm!tatica estilo <estilo>` configuram o
  time. `capitao`, `falta` e `penalti` recebem ID de titular.
- `mm!ajudajogo`: lista os comandos do jogo quando executado separado; na
  versão integrada, `mm!ajuda` mantém a ajuda antiga e a segunda página lista
  os comandos novos do jogo.

O perfil do time mostra artilheiro, jogador com mais partidas, forma dos
últimos cinco jogos e troféus. A equipe só participa com 11 titulares
compatíveis com a formação; técnico não é obrigatório. Técnico e estádio não
alteram a simulação.

As dez formações disponíveis são 4-4-2, 4-3-3, 3-5-2, 4-2-3-1, 5-3-2,
4-1-4-1, 3-4-3, 4-5-1, 5-4-1 e 4-3-2-1; cada uma define onze posições
compatíveis com o catálogo atual.

Estilos implementados: equilibrado, ataque total (multiplicadores 1,10 para o
próprio xG e 1,06 para o xG adversário) e contra-ataque (1,03 e 0,96). Capitão
e batedor de falta são registrados; não há eventos de falta. O batedor de
pênalti é priorizado em disputa por pênaltis.

`mm!time` gera uma imagem PNG com formação, nomes, posições e overalls. Usa
camisa local opcional e, na falta dela, camisa desenhada com a cor do time;
logo local também é opcional. O renderizador não baixa nem distribui imagens.

#### E3 — catálogo local de logos e uniformes

O mod local do Brasfoot foi catalogado em `data/assets/brasfoot/`, usando os
nomes dos arquivos e o sufixo do país para procurar correspondências com os
clubes. Os PNGs ficam fora do Git: a regra `data/` no `.gitignore` mantém os
assets e o banco local fora do repositório. Não copiar logos ou uniformes para
commits, releases ou hospedagem pública sem verificar as permissões de
redistribuição.

No inventário atual, o banco local contém 8.185 clubes com um caminho local de
logo. Os 796 clubes que já existiam foram associados por nome normalizado,
país da liga e, quando necessário, comparação aproximada; os 7.389 registros
restantes foram acrescentados localmente a partir do catálogo do mod. Os nomes
dos registros adicionados incluem o sufixo do arquivo para diferenciar clubes
homônimos. A associação aproximada foi forçada quando solicitado, então logos
duplicados ou de baixa confiança precisam de revisão manual antes de aparecer
no bot. A lista de associações e as camisas 1, 2 e 3 disponíveis ficam em
`data/assets/brasfoot/club_image_mapping.csv`; o inventário completo está em
`data/assets/brasfoot/team_asset_catalog.csv`. Esses índices também são locais
e não devem ser publicados junto do código.

Foram encontrados uniformes para 87 seleções, mas não escudos de seleção no
mod. Bandeiras SVG provisórias foram guardadas localmente para 85 códigos; dois
códigos ainda precisam ser identificados. O índice está em
`data/assets/brasfoot/selecoes/national_team_assets.csv`. A procedência e a
licença das bandeiras estão registradas junto aos arquivos locais.

**Estado:** os arquivos estão catalogados e os caminhos de logo estão
preenchidos no banco local. O perfil de time aceita caminhos locais de logo e
camisa dentro dessa pasta e o renderizador usa a camisa selecionada ou desenha
uma camisa com a cor do time como fallback. Os comandos não procuram
automaticamente no catálogo; o dono precisa indicar um arquivo existente.
Revisar associações ambíguas e os códigos de seleção continua pendente.

## Partidas e campeonato diário

A simulação seria apresentada como highlights minuto a minuto: gols e
assistências, cartões, pênaltis e VAR raro (estimativa inicial: um jogo em dez).
Força dos setores e uma parcela de sorte definiriam ataques, chutes e gols.
Setores: goleiro; defesa (ZAG, LD, LE); meio (MC, MEI); ataque (PE, PD, CA).
Fadiga e lesões ficariam fora da primeira versão. Os fatores exatos e seus pesos
precisam ser calibrados com partidas de teste; as regras documentadas de outros
jogos servem apenas como referência para uma implementação original.

O campeonato diário usa `America/Sao_Paulo`, com inscrições por servidor.
Executa todos-contra-todos em sequência e depois uma final entre os dois
primeiros da classificação; a final empatada vai a pênaltis. A pontuação da
fase de grupos é 3/1/0, com desempates por saldo, gols marcados, confronto
direto e nome. O campeão recebe um troféu acumulável. Pode ser iniciado
manualmente por administrador; o agendador das 19h é opt-in e fica desligado
por padrão.

### Referência inicial: Brasfoot 22–23

O manual local da edição 22–23 descreve habilidades separadas para goleiros,
agilidade, passe, armação, desarme, finalização e técnica. Também descreve
tendências ofensivas/defensivas, adequação à posição e ao lado preferido,
estilos equilibrado, ofensivo e de contra-ataque, direcionamento de ataques,
intensidade de marcação e influência do gramado. São conceitos úteis para
orientar uma simulação própria, mas não revelam a fórmula completa nem devem ser
tratados como especificação do MEMI.

Como referências qualitativas, o manual associa marcação mais intensa a menos
ataques adversários e mais faltas/cartões, e gramados piores a dificuldades de
controle técnico e passe. Seus percentuais são próprios daquela edição e não
serão copiados como pesos fixos.

A inspeção estática com REA/Ghidra identificou `bf22-23.exe` como um lançador
Windows PE de 32 bits com uma aplicação Java empacotada. A análise feita foi do
lançador; não demonstrou nem reproduziu o algoritmo de partidas. O executável
não foi iniciado. Não será copiado código, bytecode, recursos visuais ou uma
fórmula proprietária: as regras do MEMI serão implementadas de forma
independente e calibradas por observações.

### Fórmula inicial proposta para o MEMI

Esta é uma hipótese original, baseada somente em conceitos gerais como força
por setor e estilo tático. Não é uma descrição nem uma estimativa da fórmula
interna do Brasfoot. O banco atual tem overall e posição, não os atributos
individuais de passe, finalização, desarme e outros; portanto, a primeira versão
não deve inventar esses atributos.

1. Definir os 11 titulares antes do apito inicial e manter a escalação fixa
   durante os 90 minutos, sem substituições. Usar o overall armazenado,
   limitado a 1–99, e calcular a média dos titulares em cada setor:
   - `G`: goleiro;
   - `D`: defesa (ZAG, LD, LE);
   - `M`: meio (MC, MEI);
   - `A`: ataque (PE, PD, CA).
2. Converter os setores em dois índices na mesma escala de overall:

   ```text
   ataque = 0.60 × A + 0.40 × M
   resistência = 0.55 × D + 0.30 × M + 0.15 × G
   ```

   O meio participa tanto da criação quanto da proteção defensiva; o goleiro
   entra diretamente na resistência. Cada jogador conta somente no setor em
   que foi escalado.
3. Calcular os gols esperados de cada equipe por 90 minutos, sem vantagem de
   mando nem efeito de estádio na primeira calibração:

   ```text
   diferença = ataque_da_equipe - resistência_do_adversário
   xG = limitar(1.25 × exp(0.018 × diferença), 0.20, 3.50)
   ```

   `xG` é a média esperada, não um placar garantido. Uma amostra igualada começa
   em 1,25 gol esperado por equipe. Um acréscimo de 10 pontos na diferença
   aumenta essa expectativa em cerca de 20%; a exponencial cria vantagem
   gradual, e o limite impede resultados matematicamente absurdos.
4. Sortear os gols de cada lado com uma distribuição de Poisson usando o seu
   `xG`. Para narrar, sortear um minuto entre 1 e 90 para cada gol. O sorteio
   deve aceitar uma semente em testes para que a mesma partida possa ser
   reproduzida; partidas normais usam sementes diferentes.
5. Começar com estilo equilibrado (multiplicador 1,00). Como primeiro palpite a
   validar, ataque total multiplica o próprio `xG` por 1,10 e o `xG` adversário
   por 1,06; contra-ataque multiplica o próprio `xG` por 1,03 e o adversário
   por 0,96. Esses valores são parâmetros provisórios do MEMI, não números
   extraídos do Brasfoot. Não aplicar ainda bônus de gramado, estádio,
   atributos individuais, fadiga ou lesões.

Impacto de jogador fica explícito na montagem da escalação: escolher, antes da
partida, um titular 80 em vez de um 70 altera a média do setor pela diferença
entre os overalls dividida pelo número de titulares naquele setor. Não há
substituições nem mudanças de escalação durante a partida. Exemplo: escalar um
atacante 80 em vez de um atacante 70 em um setor com quatro jogadores aumenta
`A` em 2,5 e o índice de ataque em 1,5 pontos (`0.60 × 2.5`). Isso produz um
efeito pequeno e mensurável, em vez de fazer uma escolha individual decidir
sozinha o jogo.

Os pesos e multiplicadores são apenas pontos de partida. Antes de usá-los em
campeonatos, rodar milhares de partidas em cenários com elencos iguais,
diferenças graduais de overall e mandos invertidos. Verificar média de gols,
frequência de empates, taxa de vitória do elenco mais forte e simetria ao trocar
as equipes. Mudar um parâmetro por vez; manter os valores ou revisá-los conforme
os resultados, nunca apresentá-los como comportamento medido do Brasfoot.

#### Ensaio inicial: 100 partidas geradas pelo modelo

Foi executado um ensaio reproduzível com a semente `22102026`: FC Barcelona e
Real Madrid, escalações fixas 4-3-3 escolhidas pelas maiores notas disponíveis
por posição no banco local, estilo equilibrado, sem substituições e 50 jogos
com cada time listado como mandante. O modelo atual não inclui vantagem de
mando; alternar mandante/visitante apenas verifica essa simetria e não altera
`xG`.

| Medida | FC Barcelona | Real Madrid |
| --- | ---: | ---: |
| Ataque calculado | 86,40 | 86,73 |
| Resistência calculada | 85,16 | 83,54 |
| Gols esperados por jogo (`xG`) | 1,316 | 1,286 |
| Vitórias em 100 jogos | 41 | 37 |
| Empates compartilhados | 22 | 22 |
| Gols marcados em 100 jogos | 135 | 130 |

No total dos 100 jogos, o time listado como mandante venceu 44, houve 22 empates
e o visitante venceu 34. É uma amostra pequena e sujeita à sorte, não evidência
de vantagem de mando: a fórmula não tem bônus de casa. Os placares ficaram
compatíveis com cerca de 1,3 gol esperado por time; os parâmetros ainda são
provisórios e precisam de milhares de partidas sintéticas e revisão antes de
virarem regra do campeonato.

#### Ensaio: Barcelona x Sport Club Internacional

Foi repetido o mesmo modelo, agora com FC Barcelona e Sport Club Internacional
(Brasil), em 100 partidas e semente `16102026`. Foram usadas escalações fixas
4-3-3, sem substituições, montadas com os maiores overalls disponíveis por
posição. Cada equipe foi listada como mandante em 50 partidas; mando e estádio
não alteram a fórmula.

| Medida | FC Barcelona | Sport Club Internacional |
| --- | ---: | ---: |
| Ataque calculado | 86,40 | 68,47 |
| Resistência calculada | 85,16 | 68,28 |
| Gols esperados por jogo (`xG`) | 1,732 | 0,926 |
| Vitórias em 100 jogos | 56 | 19 |
| Empates compartilhados | 25 | 25 |
| Gols marcados em 100 jogos | 173 | 87 |

Dos 100 jogos, 34 tiveram vitória do mandante, 41 vitória do visitante e 25
empate. Essa diferença entre resultados de mandantes e visitantes veio da
aleatoriedade; o modelo não aplica bônus de casa. Os valores de overall do
Internacional são menores no banco atual, o que explica sua expectativa de gols
inferior nesta fórmula. A amostra ilustra o modelo, mas não valida a calibração.

### Coleta de observações de partidas

Antes de calibrar a simulação do MEMI, registrar partidas jogadas no Brasfoot
22–23, mantendo as demais condições iguais quando se comparar uma tática. Fazer
várias repetições do mesmo cenário para separar o efeito da tática da
variabilidade entre partidas. Alterar uma variável por vez e anotar, quando
disponível, ataques, finalizações, gols, posse, cartões e faltas.

| Teste | Única variável alterada | Formação e tática dos times | Gramado | Placar | Ataques/finalizações | Cartões/faltas | Observações |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Base (repetir) | Nenhuma |  |  |  |  |  |  |
| Estilo | Equilibrado / ataque / contra-ataque |  |  |  |  |  |  |
| Direção | Meio / laterais |  |  |  |  |  |  |
| Marcação | Leve / pesada / muito pesada |  |  |  |  |  |  |
| Força do elenco | Alterar apenas a força do time |  |  |  |  |  |  |

Não inferir um peso numérico a partir de uma única partida. Registrar também a
edição do jogo e eventuais condições que não puderem ser mantidas constantes.
Com esses resultados será possível escolher faixas plausíveis para o modelo
independente e validá-lo por simulação automatizada.

## Guia de implementação por fases

### Fase 1 — dados e preparação

- Manter o banco local como fonte de consulta durante as rolagens; não chamar
  APIs em tempo real para cada roll.
- Transfermarkt é a base de jogadores, clubes e valor de mercado; o estado atual
  e as regras técnicas estão em [projeto.md](./projeto.md).
- EA FC fornece overall e posição, sem substituir clube Transfermarkt.
- API-Football é opcional e limitada pela cota; testar temporadas e saldo antes
  de cargas grandes.
- Não baixar imagens sem licença de uso clara.
- Simular a distribuição e 100 mil rolls antes de fechar chances da roleta.

### Fase 2 — jogo no Discord

A implementação do jogo está dividida em etapas concluídas abaixo. O processo
`python -m src.bot` usa prefixo `mm!`, cria contas automaticamente e armazena
seu estado em SQLite (schema v9). É um modelo próprio do MEMI, inspirado em
conceitos gerais de jogos de gerenciamento; não requer Brasfoot instalado e não
reproduz código nem fórmula proprietária.

#### E1 — conexão, rolagem e claims (implementado)

Extensões carregadas: jogo, economia, rolagem, troca, time e partidas. Estão
disponíveis `mm!ajudajogo`, `mm!im`, `mm!top`, `mm!roll`, `mm!elenco` e
`mm!carteira`. Roll usa faixas de raridade, tokens e melhorias permanentes;
duplicatas, moedas e claims são persistidos e as operações de claim são
atômicas. A integração real exige `DISCORD_BOT_TOKEN` e Message Content Intent
ativada no Developer Portal.

#### E2 — economia, wishes e transações (implementado)

Inclui daily em horário de Brasília, upgrades, crates com proteção pity,
wishes e Star Wishes, demissão com reembolso e trocas bilaterais de jogador e
Memilets. Não existe upgrade de cooldown de claim nem reset desse cooldown em
crate; itens continuam fora do escopo.

#### E3 — elenco, time, formações e imagem (implementado)

Inclui filtros de elenco, dez formações, estilos táticos, titulares,
capitão/batedores, perfil com estatísticas e renderização PNG. Logo e camisa
podem ser selecionadas de arquivos locais, sob `data/assets/brasfoot/`, que não
são incluídos no Git. Sem uniforme configurado, a escalação usa uma camisa
desenhada na cor do time.

#### E4 — motor de partidas (implementado; calibração pendente)

1. Receber duas escalações válidas de 11 titulares e uma formação. A lista fica
   fixa pelos 90 minutos: não há substituições durante a partida.
2. Ler o overall (1–99) e a posição dos titulares. Calcular a média de overall
   dos grupos: goleiro (`G`), defesa (`D` = ZAG/LD/LE), meio (`M` = MC/MEI) e
   ataque (`A` = PE/PD/CA).
3. Resumir cada time em duas notas: `ataque = 0,60 × A + 0,40 × M` e
   `resistência = 0,55 × D + 0,30 × M + 0,15 × G`. Assim, atacantes e meias
   ajudam a criar jogadas; defesa, meio e goleiro ajudam a impedir gols.
4. Para cada time, comparar seu ataque com a resistência do adversário e
   calcular gols esperados para 90 minutos:

   ```text
   diferença = ataque_do_time - resistência_do_adversário
   xG = limitar(1,25 × exp(0,018 × diferença), 0,20, 3,50)
   ```

   `xG` é uma média, não o placar. Times equilibrados começam em torno de 1,25
   gol esperado cada. Uma diferença positiva aumenta a expectativa do time mais
   forte gradualmente; os limites evitam médias extremas.
5. Sortear os gols de cada equipe pela distribuição de Poisson com seu `xG` e
   sortear um minuto para cada gol. Uma semente configurável torna os testes
   repetíveis. A primeira versão não dá vantagem ao mandante nem bônus por
   estádio, gramado, fadiga ou atributos que o banco ainda não armazena.
6. O motor atribui autores dos gols por peso de posição e overall, e minuto
   aleatório entre 1 e 90. As partidas são reproduzíveis com RNG semeado nos
   testes. Não há substituições, lesões, faltas ou vantagem de mando.
7. Os multiplicadores provisórios de estilo e a fórmula precisam de ensaio
   estatístico e equilíbrio antes de habilitar campeonatos automáticos.

#### E5 — amistosos e campeonato diário (implementado; agendamento opt-in)

`mm!amistoso @pessoa` simula duas equipes completas. `mm!campeonato inscrever`
inscreve, `mm!campeonato` consulta a tabela e `mm!campeonato iniciar` executa
manualmente (requer Gerenciar servidor). A fase de grupos é todos-contra-todos;
os dois primeiros avançam à final, que usa pênaltis em caso de empate. Os
resultados e os titulares usados ficam persistidos para estatísticas e
reexecução segura.

Para agendamento às 19:00 de Brasília, o administrador configura o canal com
`mm!campeonato canal [#canal]`; a variável global
`MEMI_CHAMPIONSHIP_SCHEDULER=1` precisa estar definida no `.env` e o servidor
deve ser ativado com `mm!campeonato ativar`. O padrão seguro é desligado.

Uma troca de jogador afeta o resultado apenas na montagem da escalação: por
exemplo, um overall 80 em vez de 70 aumenta em 2,5 a média de um setor com
quatro titulares; se for um atacante, isso aumenta o índice de ataque em 1,5
(`0,60 × 2,5`). Durante o jogo, a escalação não muda.

As etapas do jogo têm testes automatizados; a conexão a um servidor Discord e
o agendador real exigem credenciais e configuração fora do ambiente de teste.
O scheduler continua desligado por padrão. Itens, aposentados e mascotes podem
ser avaliados depois desta primeira versão.

### Fase futura — quiz de música

Ideia separada da fase de futebol: tocar músicas em chamadas de voz e receber
respostas no chat, com faixas escolhidas pelos participantes ou de playlists.
Fontes, permissões e licenças precisam ser resolvidas antes de desenvolver.

## Comandos implementados

| Comando | Função |
| --- | --- |
| `mm!ajudajogo`, `mm!im`, `mm!top` | Ajuda do jogo, busca de jogadores e ranking |
| `mm!roll` | Rola jogador, aplica duplicatas e inicia claim |
| `mm!daily`, `mm!upgrade`, `mm!crates` | Economia e progressão |
| `mm!wish`, `mm!starwish` | Lista, adiciona ou remove wishes |
| `mm!carteira`, `mm!demitir` | Consulta saldo e libera jogador |
| `mm!troca` | Oferece, aceita ou recusa troca bilateral |
| `mm!elenco [@pessoa] [filtro]` | Consulta elenco com filtros |
| `mm!time [campo valor]` | Consulta ou personaliza perfil e escalação |
| `mm!escalar <id>` | Escala ou remove titular |
| `mm!tatica <operação> <valor>` | Formação, estilo, capitão e cobradores |
| `mm!amistoso @pessoa` | Simula um amistoso |
| `mm!campeonato [operação]` | Inscreve, consulta, inicia ou configura agenda |

## Validação pendente e alertas

- Calibrar estatisticamente o motor com milhares de partidas sintéticas,
  medindo médias de gols, empates, taxa de vitória por diferença de overall e
  simetria ao inverter os times.
- Validar a distribuição da roleta por pelo menos 100 mil rolls sintéticos;
  chances de faixa e crates são parâmetros de jogo, não resultados do Brasfoot.
- Fazer smoke test com um bot de teste em servidor Discord, sem expor token;
  conferir permissões de intents, botões, anexos PNG e execução do scheduler.
- Revisar manualmente correspondências ambíguas do catálogo local de logos e
  uniformes. Os arquivos e índices permanecem fora do Git.
- Verificar separadamente a licença de qualquer foto/escudo antes de baixar ou
  redistribuir. A escalação atual usa somente arquivos locais indicados pelo
  administrador do time ou fallback gerado.
- Revisar aliases de nomes antes de importar EA; nomes abreviados como “Vini
  Jr.” podem deixar de casar apesar de nascimento e clube compatíveis.
- O importador EA exclui futebol feminino por padrão; `--include-women` permite
  inclusão explícita. A associação por alias é aplicada somente no `--dry-run`
  e requer data de nascimento igual.
- A importação EA deve ser precedida pelo ensaio `--dry-run`; a calibração de
  overall e a reescala para 1–99 dependem de revisar a amostra e a distribuição.
- Os pesos do motor são provisórios: o campeonato automático permanece
  desativado por padrão até a calibração e validação operacional.
- A observação de partidas do Brasfoot é opcional para comparação qualitativa,
  não requisito para desenvolver ou usar o motor próprio do MEMI.

## Estado dos dados de origem

O schema atual é v9; as migrações preservam o banco existente. Os números de
importação abaixo são do snapshot de dados documentado em 15/10/2026, não uma
garantia de que o pool local não tenha mudado. O resumo técnico está em
[projeto.md](./projeto.md).

O ensaio local FC27 do snapshot de 12/09/2026 foi executado sem gravar no banco:
12.174 casamentos exatos e 999 por alias com Transfermarkt, 317 casos para
revisão, 4.359 novos em potencial, nenhum excluído por clube/liga brasileira e
1.940 excluídos por futebol feminino. A correlação entre overall EA e a
estimativa de mercado foi 0,7799 em 12.169 jogadores comparáveis; a diferença
média foi +6,59 pontos (EA menos mercado). São números de diagnóstico, não uma
decisão final de calibração nem uma importação real.
