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

O comando de rolagem ainda precisa de nome. A ideia inicial é oferecer 10 rolls
por ciclo de 30 minutos, liberados nos minutos `:10` e `:40` de cada hora.

Em vez de sortear uniformemente do pool, a roleta escolhe primeiro uma faixa de
overall e depois um jogador livre daquela faixa. A tabela abaixo é uma proposta
para validar por simulação:

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

A soma das faixas 87+ é 0,15% por roll. A simulação deve considerar a quantidade
real de jogadores livres em cada faixa e cair para uma faixa disponível quando
uma faixa estiver vazia. A chance de itens por roll ainda precisa ser definida;
5% é apenas uma sugestão inicial.

Claim proposto:

- Um claim por hora inicialmente; upgrades podem reduzir o intervalo até 30
  minutos.
- Quem rolou tem cinco segundos de exclusividade. Upgrades acrescentariam um
  segundo por nível, até 15 segundos.
- Pessoas com o jogador na wish também poderiam disputar a janela; quem
  reivindicar primeiro fica com ele.
- Um bônus adicional de exclusividade para wish na própria rolagem foi sugerido
  em três segundos, mas o valor ainda precisa ser decidido.

## Posse, duplicatas e trocas

- Cada jogador pertence a uma pessoa por vez.
- Rolar novamente um jogador que já pertence à própria pessoa concede uma bola.
- Cada bola acrescenta um ponto de overall e 100 Memilets, até dez pontos de
  bônus e overall máximo 99.
- Bolas acima do limite convertem-se em 500 Memilets.
- `mm!demitir` libera o jogador e remove suas bolas; o reembolso segue a regra de
  mercado descrita abaixo.
- `mm!troca` permite negociar jogadores e itens, com Memilets opcionais e
  confirmação dos dois lados.

O limite de claims é a proteção principal contra o ciclo de reivindicar e
demitir para obter reembolsos.

## Wishes

Cada pessoa começaria com cinco wishes normais e um Star Wish. Wishes normais
não aumentam a chance de rolagem; oferecem proteção durante a janela de claim.
Um Star Wish aumentaria a chance relativa do personagem em aproximadamente 25%
(por exemplo, `0,0100%` para `0,0125%`), não em 25 pontos percentuais.

O número de slots pode aumentar com upgrades. Os nomes e comandos para wish e
Star Wish ainda precisam ser definidos.

## Economia e upgrades

### Daily

`mm!daily` começaria pagando entre 200 e 300 Memilets. O mínimo ficaria em 200;
cada nível aumentaria o teto em 100, até 2.000.

### Upgrades planejados

| Upgrade | Efeito | Custo proposto |
| --- | --- | --- |
| Roll permanente | +1 roll por ciclo | 5k, aumentando 5k a cada compra |
| Wish permanente | +1 slot de wish | 3k, aumentando 3k a cada compra |
| Daily | +100 no teto, até 2.000 | 5k por nível |
| Intervalo de claim | Reduz o cooldown até 30 minutos | A definir |
| Exclusividade | +1 segundo por nível, até 15 segundos | A definir |
| Carta de item | Um roll adicional de item por ciclo | 15k, uma compra |

O elenco não teria limite de slots.

### Crates

`mm!crates` abriria caixas por 500 Memilets cada. Distribuição inicial proposta:

| Prêmio | Chance |
| --- | ---: |
| 200–450 Memilets | 58% |
| Rolls instantâneos (+1 a +5) | 25% |
| Reset do cooldown de claim | 16% |
| +1 wish permanente | 0,5% |
| +1 slot permanente de wish | 0,3% |
| +1 roll permanente | 0,2% |

Como proteção contra azar, a cada 100 caixas sem prêmio permanente sairia um
permanente, escolhido com pesos 50% wish, 30% slot de wish e 20% roll. Essa
proteção teria contador próprio e não mudaria o preço dos upgrades. Chances e
custos ainda são propostas, não valores implementados.

### Valor de mercado e reembolso

Faixa de exibição proposta: overall 1 vale €5 mil e overall 99 vale €100
milhões, usando:

```text
5.000 × 20.000 ^ ((overall − 1) / 98)
```

`mm!demitir` devolveria 0,05% do valor de mercado em Memilets
(`valor × 0,0005`). A tabela de valores deve ser calibrada e validada antes da
implementação.

## Time, elenco e tática

Comandos propostos:

- `mm!time`: perfil e personalização do time, incluindo nome, cor, logo,
  mascote, técnico e estádio.
- `mm!elenco [@pessoa]`: elenco em ordem de aquisição por padrão; filtros por
  overall, posição e time/liga de origem.
- `mm!escalar <jogador>`: escala o jogador na sua posição única.
- `mm!tatica`: formação, estilo, capitão e batedores.
- `mm!ajuda`: lista paginada dos comandos do jogo.

O perfil do time mostraria artilheiro, jogador com mais partidas, forma dos
últimos cinco jogos e troféus. A equipe só participaria do campeonato com 11
titulares; técnico não seria obrigatório. Técnico e estádio não alterariam a
simulação inicialmente; estádio poderia influenciar somente o público.

Formações planejadas incluem 4-4-2, 4-3-3, 3-5-2, 4-2-3-1, 5-3-2, 4-1-4-1,
3-4-3, 4-5-1 e 5-4-1. A lista final deve conter dez ou mais formações e definir
as posições exatas requeridas.

Estilos iniciais: equilibrado, contra-ataque e ataque total, com efeitos
moderados (10–15%) e compensações claras. Capitão seria cosmético; batedores de
falta e pênalti influenciariam a conversão desses lances.

Uma imagem de escalação gerada automaticamente mostraria os 11 jogadores no
campo, formação, estilo e capitão. Foto seria usada quando disponível; caso
contrário, uma camisa com a cor do time. Imagens baixadas devem ficar em cache.

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
preenchidos no banco local, mas o renderizador do bot ainda não consome o
catálogo nem as camisas. Próximos passos: revisar os casos ambíguos, identificar
os códigos de seleção pendentes e integrar os caminhos ao gerador de imagem;
testar logos ausentes, uniformes alternativos e o fallback para camisa com a
cor do time.

## Partidas e campeonato diário

A simulação seria apresentada como highlights minuto a minuto: gols e
assistências, cartões, pênaltis e VAR raro (estimativa inicial: um jogo em dez).
Força dos setores e uma parcela de sorte definiriam ataques, chutes e gols.
Setores: goleiro; defesa (ZAG, LD, LE); meio (MC, MEI); ataque (PE, PD, CA).
Fadiga e lesões ficariam fora da primeira versão. Os fatores exatos e seus pesos
precisam ser calibrados com partidas de teste; as regras documentadas de outros
jogos servem apenas como referência para uma implementação original.

Às 19h, em fuso ainda a confirmar, times completos disputariam um campeonato
diário. O campeão receberia um troféu acumulável no perfil `mm!time`. Formato,
pontuação e desempates ainda precisam ser definidos.

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

Ordem sugerida: conexão e rolagem; daily, upgrades e claim; elenco, time,
formações e imagem; depois motor de partidas e campeonato diário. A simulação
tem fórmula proposta, mas ainda não foi implementada no bot. É um modelo próprio
do MEMI, inspirado em ideias gerais de jogos de gerenciamento; não requer ter o
Brasfoot instalado e não reproduz seu código ou fórmula interna.

#### E4 — implementar e validar a simulação

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
6. Começar pelo estilo equilibrado. Os multiplicadores provisórios para ataque
   total e contra-ataque descritos na fórmula precisam ser testados; não são
   dados do Brasfoot. Depois, acrescentar narração de lances e, em etapas
   separadas, eventos como cartões, pênaltis e VAR.
7. Testar elencos iguais e com diferenças graduais de overall; inverter as
   equipes e o mando; rodar 100 partidas para inspeção e milhares para validar
   médias de gols, empates e vantagem esperada do elenco melhor. Ajustar um
   parâmetro de cada vez antes de habilitar campeonato competitivo.

Uma troca de jogador afeta o resultado apenas na montagem da escalação: por
exemplo, um overall 80 em vez de 70 aumenta em 2,5 a média de um setor com
quatro titulares; se for um atacante, isso aumenta o índice de ataque em 1,5
(`0,60 × 2,5`). Durante o jogo, a escalação não muda.

Depois de validar o amistoso, ligar o motor a um comando de teste e só então ao
campeonato diário. O agendador das 19h fica desligado até os testes do
campeonato terminarem com sucesso. Itens, aposentados, mascotes e uniformes
podem ser avaliados depois da primeira versão do jogo.

### Fase futura — quiz de música

Ideia separada da fase de futebol: tocar músicas em chamadas de voz e receber
respostas no chat, com faixas escolhidas pelos participantes ou de playlists.
Fontes, permissões e licenças precisam ser resolvidas antes de desenvolver.

## Comandos planejados

| Comando | Função prevista |
| --- | --- |
| `mm!(roletar a definir)` | Rola personagens |
| `mm!daily` | Recebe Memilets diários |
| `mm!upgrade` | Compra upgrades |
| `mm!crates` | Abre crates |
| `mm!time` | Mostra e personaliza o time |
| `mm!elenco [@pessoa]` | Mostra um elenco |
| `mm!escalar <jogador>` | Escala um jogador |
| `mm!tatica` | Configura formação e tática |
| `mm!troca` | Inicia troca entre pessoas |
| `mm!demitir` | Libera um jogador |
| `mm!ajuda` | Ajuda paginada do jogo |

## Decisões pendentes e alertas

- Definir nome do jogo e comando de rolagem.
- Validar chance das faixas de overall e chance de item.
- Definir os custos de redução do claim e de exclusividade.
- Definir bônus exato de wish na própria rolagem.
- Definir formato, desempate e fuso do campeonato.
- Fechar lista de formações e decidir se overall aparece na imagem da escalação.
- Escolher fonte para fotos com licença clara.
- Revisar aliases de nomes antes de importar EA; nomes abreviados como “Vini
  Jr.” podem deixar de casar apesar de nascimento e clube compatíveis.
- O importador EA exclui futebol feminino por padrão; `--include-women` permite
  inclusão explícita. A associação por alias é aplicada somente no `--dry-run`
  e requer data de nascimento igual.
- A importação EA deve ser precedida pelo ensaio `--dry-run`; a calibração de
  overall e a reescala para 1–99 dependem de revisar a amostra e a distribuição.
- Os pesos do motor de partidas são provisórios: validar com simulações
  automatizadas antes de ativar o campeonato. Observações do Brasfoot são
  opcionais para comparação qualitativa, não são requisito para desenvolver ou
  usar o motor próprio do MEMI.

## Estado conhecido em 15/10/2026

O banco de dados está migrado para schema v6, com 50.149 jogadores, 796 clubes,
IDs Transfermarkt únicos e atividade operacional baseada somente em
`last_season >= 2025`. O pool reportado é 18.375 jogadores ativos, com overall
e posição. O resumo técnico e os dados completos ficam em [projeto.md](./projeto.md).

O ensaio local FC27 do snapshot de 12/09/2026 foi executado sem gravar no banco:
12.174 casamentos exatos e 999 por alias com Transfermarkt, 317 casos para
revisão, 4.359 novos em potencial, nenhum excluído por clube/liga brasileira e
1.940 excluídos por futebol feminino. A correlação entre overall EA e a
estimativa de mercado foi 0,7799 em 12.169 jogadores comparáveis; a diferença
média foi +6,59 pontos (EA menos mercado). São números de diagnóstico, não uma
decisão final de calibração nem uma importação real.
