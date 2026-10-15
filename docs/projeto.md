# Bot Memi (Mudae de futebol) — fase 1: coleta de dados

Para a visão do produto, mecânicas planejadas e roteiro do bot no Discord,
consulte o [guia de ideias do jogo](./guia-ideias-jogo.md).

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
- Overall inteiro de 1 a 99. Priorizar a nota do EA FC; estimar os demais
  somente a partir do valor de mercado, com ajustes manuais auditáveis.
- Valor de mercado é armazenado em euros; para exibição, limitar a faixa entre
  €5 mil e €100 milhões.
- Nunca unir automaticamente jogadores ambíguos. Exportar candidatos para
  revisão manual.
- O clube atual vem do Transfermarkt; o clube EA é apenas metadado da fonte e
  nunca sobrescreve o clube Transfermarkt.
- Usar o ID do Transfermarkt como chave única e importar por upsert. Cruzar
  fontes por nome normalizado e data de nascimento, sem usar clube como critério
  de atividade, cruzamento ou cálculo de overall.
- Exibir homônimos com nome, clube e ano de nascimento; se ainda houver colisão,
  acrescentar o ID interno.
- Para EA sem data de nascimento, tentar nome normalizado, nacionalidade e idade
  na data do snapshot (margem de um ano). Casos ambíguos ou sem dados suficientes
  vão para revisão e não são inseridos como novos.
- Jogadores que só existam no EA podem entrar como novos, exceto quando o clube
  ou a liga do registro EA for brasileira. Brasileiros que atuam fora do Brasil
  não são excluídos por cidadania.
- Se houver múltiplas edições EA, a edição mais recente prevalece. Notas EA são
  registradas com `overall_source=EA` e a edição correspondente.
- Guardar edição do overall e temporada de cada fonte. Na temporada 2026/27,
  considerar ativo pelo critério Transfermarkt quando `last_season >= 2025`.
  Clube não participa da regra de atividade. É uma heurística, não prova de
  atividade em campo: o
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
Para a temporada 2026/27, jogadores com `last_season >= 2025` são considerados
ativos, mesmo sem clube atual informado. Clube não é critério de atividade. O
dataset publicado está defasado em relação à temporada 2026/27; esse campo é um
critério operacional, não confirmação de que o atleta está jogando atualmente.

## EA FC

Salve o CSV da edição EA FC mais recente em `data/raw/` (atualmente FC27;
FC26 também é aceito). Elencos e notas de edições antigas podem estar
desatualizados. Antes de importar, confira as categorias reais com:

```bash
python -m src.import_ea --show-positions
```

Antes da carga, valide o cruzamento sem alterar o banco:

```bash
python -m src.import_ea --input data/raw/players.csv --dry-run
```

O ensaio opera sobre uma cópia SQLite temporária e relata o cruzamento,
ambiguidades, novos registros, exclusões por clubes/ligas brasileiros, relação
entre overall EA e estimativa de mercado, e os 30 maiores overalls. O arquivo de
revisão é salvo em `data/review/ea_matches_dry_run.csv`; o banco original não é
modificado.

O importador aceita cabeçalhos comuns EA/Sofifa e o formato FC27, valida
overall entre 1 e 99 e interrompe antes de gravar se encontrar posição principal
ou alternativa sem mapeamento. Para dados com mais de uma posição, a primeira
posição listada é considerada a principal. O clube EA é guardado apenas como
metadado da fonte; não preenche nem sobrescreve `players.club_name`. O
importador cruza por nome e data de nascimento; na ausência da data, usa nome,
nacionalidade e idade no snapshot. Ambiguidades e identidades insuficientes vão
para `data/review/ea_matches.csv`, sem inserção como novo. Jogadores EA-only de
clubes ou ligas brasileiras são excluídos; brasileiros em clubes estrangeiros
não são excluídos por cidadania. Entre edições, a mais recente prevalece.

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
Clube não é critério de correspondência, pois pode estar desatualizado nos dados
EA. Exporta candidatos acima de 75/100 para
`data/review/player_matches.csv`. Datas conhecidas conflitantes descartam o
candidato. Todos os pares ficam pendentes para revisão; o processo não faz merge
automático.

## Overall

Execute `python -m src.overall --limit 100` para recalcular um lote de teste, ou
sem `--limit` para atualizar todos. Um ajuste manual em `overall_adjustments`
tem prioridade; em seguida é preservada a nota EA FC. Para os demais, usa-se
uma escala logarítmica entre €5 mil/overall 30 e €100 milhões/overall 88. O
resultado fica limitado a 1-99; clube e liga não participam da fórmula.

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
