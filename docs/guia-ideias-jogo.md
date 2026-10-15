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

## Partidas e campeonato diário

A simulação seria apresentada como highlights minuto a minuto: gols e
assistências, cartões, pênaltis e VAR raro (estimativa inicial: um jogo em dez).
Overall dos setores e uma parcela de sorte definiriam ataques, chutes e gols.
Setores: goleiro; defesa (ZAG, LD, LE); meio (MC, MEI); ataque (PE, PD, CA).
Fadiga e lesões ficariam fora da primeira versão.

Às 19h, em fuso ainda a confirmar, times completos disputariam um campeonato
diário. O campeão receberia um troféu acumulável no perfil `mm!time`. Formato,
pontuação e desempates ainda precisam ser definidos.

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

Ordem sugerida: conexão e rolagem; daily, upgrades e claim; elenco e time;
formações e imagem; simulação e campeonato diário. Em seguida, avaliar ícones,
aposentados, mascotes, uniformes e outras categorias.

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
- A decisão de produto registrada no documento original é deixar futebol
  feminino fora da fase 1. O importador ainda precisa implementar e testar esse
  filtro antes de uma importação real.
- A importação EA deve ser precedida pelo ensaio `--dry-run`; a calibração de
  overall e a reescala para 1–99 dependem de revisar a amostra e a distribuição.

## Estado conhecido em 15/10/2026

O banco de dados está migrado para schema v6, com 50.149 jogadores, 796 clubes,
IDs Transfermarkt únicos e atividade operacional baseada somente em
`last_season >= 2025`. O pool reportado é 18.375 jogadores ativos, com overall
e posição. O resumo técnico e os dados completos ficam em [projeto.md](./projeto.md).

O ensaio local FC27 do snapshot de 12/09/2026 foi executado sem gravar no banco:
12.174 casamentos com Transfermarkt, zero ambíguos pela regra estrita, 7.614
novos em potencial e um excluído por clube/liga brasileira. A correlação entre
overall EA e a estimativa de mercado foi 0,7746 em 11.257 jogadores comparáveis;
a diferença média foi +6,52 pontos (EA menos mercado). Esses números são
diagnóstico, não decisão final de calibração. Alias de nomes, futebol feminino,
licenças e reescala continuam pendentes antes de importar EA.
