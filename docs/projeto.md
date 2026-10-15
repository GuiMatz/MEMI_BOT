# Bot Memi (Mudae de futebol) — fase 1: coleta de dados

## Objetivo

Montar uma base local, reproduzível e revisável para o futuro bot de futebol.
Nesta fase, o foco é coletar, normalizar, cruzar e relatar dados; não implementar
comandos Discord.

## Ambiente e armazenamento

- Python com dependências declaradas em `requirements.txt`.
- Banco SQLite em `data/memi.sqlite`.
- CSVs originais em `data/raw/` e imagens em `data/photos/`.
- A pasta `data/`, bancos e segredos ficam fora do Git.
- Chaves de serviço são lidas exclusivamente do `.env`.

## Fontes, em ordem

1. Transfermarkt Datasets: jogadores e clubes em CSV.
2. Dataset EA FC mais recente disponível (atualmente FC27): overall e posição,
   obtidos manualmente; não usar clube EA como clube atual.
3. API-Football: cota gratuita limitada; conferir em cada resposta o cabeçalho
   `x-ratelimit-requests-remaining` e parar antes de esgotar a cota.
4. TheSportsDB: fotos, escudos e estádios.

O volume esperado é de aproximadamente 150 mil jogadores. Importadores devem
processar em lotes e oferecer `--limit` para testes pequenos antes de cargas
completas.

## Modelo e regras de dados

- Entidades: jogadores, clubes, ligas, estádios e treinadores.
- `player_sources` registra a fonte e o identificador original de cada jogador
  para permitir rastrear e cruzar dados sem perder a proveniência.
- Cada jogador possui uma única posição de jogo: GOL, ZAG, LD, LE, MC, MEI, PE,
  PD ou CA. Normalizações: VOL→MC, SA→CA, ALA→LD/LE (requer revisão, pois a ala
  pode corresponder a mais de um lado).
- Overall inteiro de 1 a 99. Priorizar a nota do EA FC; estimar os demais a
  partir de valor de mercado e peso da liga, com ajustes manuais auditáveis.
- Valor de mercado é armazenado em euros; para exibição, limitar a faixa entre
  €5 mil e €100 milhões.
- Nunca unir automaticamente jogadores ambíguos. Exportar candidatos para
  revisão manual.
- O clube atual vem do Transfermarkt; o clube EA é apenas metadado da fonte.
- Cruzar fontes por nome normalizado e data de nascimento; usar clube somente
  para desempatar homônimos.
- Guardar edição do overall e temporada de cada fonte. Na temporada 2026/27,
  considerar ativo pelo critério Transfermarkt quando `last_season >= 2025` e
  `current_club_id` estiver preenchido; correspondência com a tabela `clubs`
  não é obrigatória. É uma heurística, não prova de atividade em campo: o
  dataset publicado informa dados até 6 de julho de 2026, sem elencos 2026/27.
  A API-Football pode confirmar atividade adicionalmente.
- Jogadores com atividade desconhecida ou sem overall não entram no pool da
  roleta. Dados desatualizados/inativos ficam candidatos à fase 2.
- Fotos e escudos são opcionais; falhas de imagem não impedem a importação.

## Transfermarkt

Obtenha `players.csv.gz` e `clubs.csv.gz` da página pública do
[Transfermarkt Datasets](https://github.com/dcaribou/transfermarkt-datasets/wiki)
e salve-os em `data/raw/`. Primeiro execute:

```bash
python -m src.import_tm --show-positions
```

O comando de importação sempre imprime as categorias distintas antes de gravar
dados. Para validar um lote pequeno:

```bash
python -m src.import_tm --limit 100
```

Sem `--limit`, importa todos os jogadores. Uma subposição desconhecida faz a
importação parar antes de gravar dados. Jogadores sem subposição mantêm a
posição ampla original e ficam com posição de jogo nula.

Mapeamento revisado das subposições observadas:

| Transfermarkt | Posição do jogo |
| --- | --- |
| Attacking Midfield | MEI |
| Central Midfield, Defensive Midfield | MC |
| Centre-Back | ZAG |
| Centre-Forward, Second Striker | CA |
| Goalkeeper | GOL |
| Left Midfield, Left Winger | PE |
| Left-Back | LE |
| Right Midfield, Right Winger | PD |
| Right-Back | LD |

O dataset testado contém 50.149 jogadores e 796 clubes. Sua coluna `position`
contém Attack, Defender, Goalkeeper, Midfield e Missing; `sub_position` contém
as categorias acima e está vazia para parte dos jogadores. Linhas com posição
geral conhecida usam o fallback indicado; Missing permanece sem inferência.
Para a temporada 2026/27, jogadores com `last_season >= 2025` e
`current_club_id` preenchido são considerados ativos. O ID de clube não precisa
estar em `clubs.csv.gz`; nesses casos `players.club_id` permanece nulo, embora
o jogador possa entrar no pool se tiver overall. O dataset publicado está
defasado em relação à temporada 2026/27; esse campo é um critério operacional,
não confirmação de que o atleta está jogando atualmente.

## EA FC

Salve o CSV da edição EA FC mais recente em `data/raw/` (atualmente FC27;
FC26 também é aceito). Elencos e notas de edições antigas podem estar
desatualizados. Antes de importar, confira as categorias reais com:

```bash
python -m src.import_ea --show-positions
```

O importador aceita cabeçalhos comuns EA/Sofifa e o formato FC27, valida
overall entre 1 e 99 e interrompe antes de gravar se encontrar posição principal
ou alternativa sem mapeamento. Para dados com mais de uma posição, a primeira
posição listada é considerada a principal. O clube EA é guardado apenas como
metadado da fonte; não preenche nem sobrescreve `players.club_id`. Cada edição
EA é uma fonte separada até a etapa de revisão de correspondências.

| EA FC | Posição do jogo |
| --- | --- |
| ST, CF | CA |
| LW, LM | PE |
| RW, RM | PD |
| CAM | MEI |
| CM, CDM | MC |
| LB, LWB | LE |
| RB, RWB | LD |
| CB | ZAG |
| GK | GOL |

## Cruzamento de fontes

Execute:

```bash
python -m src.match
```

O processo exige data de nascimento igual e compara nomes usando RapidFuzz.
Clube serve como critério de desempate, nunca como bloqueio, pois pode estar
desatualizado nos dados EA. Exporta candidatos acima de 75/100 para
`data/review/player_matches.csv`. Datas conhecidas conflitantes descartam o
candidato. Todos os pares ficam pendentes para revisão; o processo não faz merge
automático.

## Overall

Execute `python -m src.overall --limit 100` para recalcular um lote de teste, ou
sem `--limit` para atualizar todos. Um ajuste manual em `overall_adjustments`
tem prioridade; em seguida é preservada a nota EA FC. Para os demais, usa-se
uma escala logarítmica entre €5 mil/overall 30 e €100 milhões/overall 88,
multiplicada pelo peso da liga (padrão 1,0; permitido entre 0,5 e 1,5). O
resultado fica limitado a 1-99.

Os pesos podem ser configurados na tabela `league_overall_weights`; a fórmula é
transparente para permitir calibrar a distribuição após importar os CSVs.

## API-Football

Configure `API_FOOTBALL_KEY` em `.env`. Use primeiro uma temporada e liga
permitidas no seu plano, com um limite pequeno:

```bash
python -m src.api_football --league ID_DA_LIGA --season ANO --limit 1
```

O cliente confere `x-ratelimit-requests-remaining` em cada resposta e persiste o
saldo observado por dia UTC. Com saldo zero, nenhuma chamada nova é feita.
Importa 20 jogadores por página e registra página/offset para retomar após
interrupção. A chamada de teste salva o offset para a próxima execução; os
registros da API-Football permanecem como fonte independente até revisão de
correspondências. A temporada consultada é registrada em cada fonte. Essa API
é opcional para atividade: a regra Transfermarkt por `last_season` e clube atual
preenchido também confirma atividade. Jogadores sem overall ficam fora do pool.

## Fotos e escudos

Salve as credenciais no `.env` e execute:

```bash
python -m src.photos --limit 100
python -m src.photos --limit 100 --thesportsdb
```

O primeiro modo baixa URLs já obtidas da API-Football. O segundo também busca
imagens ausentes no TheSportsDB, mas só aceita resultados com nome exato
normalizado. Arquivos vão para `data/photos/`; URLs vazias, 404 e recursos que
não sejam imagens são pulados. Erros de rede e respostas inesperadas param a
execução com mensagem explícita. O repositório Transfermarkt declara CC0 1.0
para o dataset, mas isso não concede por si só direitos sobre as imagens
externas apontadas por `image_url`; não as baixe nem reutilize sem confirmar
uma licença ou autorização própria.

## Relatório

Execute `python -m src.report` para ver o total de jogadores, a contagem por
fonte, posição e faixas 1-59, 60-69, 70-79, 80-86, 87-89, 90-94, 95-97 e
98-99; tops 30 por overall e valor de mercado; atividade, elegibilidade para
roleta, jogadores sem posição, revisão, cruzamentos confirmados ou pendentes,
clubes sem jogadores e fotos ausentes.

## Schema inicial

O schema SQLite é criado por `python -m src.db`. O script é idempotente, migra
bancos existentes sem apagar dados e usa chaves estrangeiras, índices e
restrições para proteger os dados. `player_sources` mantém o clube e a posição
originais de cada fonte quando disponíveis.

## Estado atual e próximos passos

- Schema, documentação, dependências e ambiente virtual inicializados.
- Transfermarkt completo importado: 50.149 jogadores e 796 clubes. Pelo critério
  aprovado, 22.292 estão marcados ativos; o pool contém 18.404 com overall.
  27.857 permanecem com atividade desconhecida, 8.621 sem overall e 586 sem
  posição específica. 2.986 IDs de clube ativos não existem em `clubs.csv.gz`;
  esses jogadores ficam sem clube vinculado. Os arquivos brutos são ignorados
  pelo Git.
- O CSV FC27 em Downloads é um snapshot de 2026-09-12. Há no Kaggle um snapshot
  posterior, coletado em 2026-10-11, ainda não baixado nem importado. O
  importador precisa ser ajustado e testado com o arquivo real antes de usá-lo.
- Cruzamento, overall, cliente API-Football retomável, coletor de imagens e
  relatório implementados e cobertos por testes unitários. Ainda não foram
  executadas chamadas reais às APIs.
- Próximo: adicionar os CSVs EA FC, inspecionar valores/posições com
  `--show-positions`, importar primeiro `--limit 100` e revisar as linhas no
  SQLite Viewer.
- Antes da coleta completa na API-Football, configurar a chave privada em `.env`
  e testar individualmente as temporadas permitidas pelo plano.
- Após importar tudo, revisar candidatos em `data/review/player_matches.csv`,
  ajustar pesos de liga e validar se poucos jogadores ficam acima de overall
  87.

## Validação operacional

1. Executar cada importação com `--limit 100` e revisar os dados no SQLite
   Viewer antes da carga completa.
2. Conferir a distribuição de overall e assegurar que poucos jogadores tenham
   nota superior a 87.
3. Consultar quais temporadas o plano da API-Football permite antes de iniciar
   a coleta.
4. Registrar e revisar manualmente merges ambíguos.
