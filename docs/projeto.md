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
2. EA FC 24/25: overall e posição, obtidos manualmente no Kaggle.
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
as categorias acima e está vazia para parte dos jogadores. Não há inferência de
posição de jogo para linhas sem subposição.

## EA FC

Salve os CSVs do EA FC 24/25 em `data/raw/`. Antes de importar, confira as
categorias reais com:

```bash
python -m src.import_ea --show-positions
```

O importador aceita cabeçalhos comuns dos exports EA/Sofifa, valida overall
entre 1 e 99 e interrompe antes de gravar se encontrar posição sem mapeamento.
Para dados com mais de uma posição, a primeira posição listada é considerada a
principal, mantendo-se a lista original em `player_sources` para auditoria.
Cada arquivo EA é uma fonte separada; os registros não são unidos ao
Transfermarkt nem a outro arquivo EA até a etapa de revisão de correspondências.

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

O processo bloqueia pares por clube normalizado ou data de nascimento igual,
compara nomes usando RapidFuzz e exporta candidatos acima de 75/100 para
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
correspondências.

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
execução com mensagem explícita.

## Relatório

Execute `python -m src.report` para ver o total de jogadores, a contagem por
fonte, posição e faixa de overall, além de notas 87+, jogadores sem posição,
cruzamentos confirmados ou pendentes, clubes sem jogadores e fotos ausentes.

## Schema inicial

O schema SQLite é criado por `python -m src.db`. O script é idempotente, migra
bancos existentes sem apagar dados e usa chaves estrangeiras, índices e
restrições para proteger os dados. `player_sources` mantém o clube e a posição
originais de cada fonte quando disponíveis.

## Estado atual e próximos passos

- Schema, documentação, dependências e ambiente virtual inicializados.
- Transfermarkt importado em amostra real: 100 jogadores e 796 clubes. O dataset
  disponível contém 50.149 jogadores; os arquivos brutos continuam locais e
  ignorados pelo Git.
- Importador EA FC implementado, mas a inspeção das categorias reais depende dos
  CSVs do Kaggle em `data/raw/`.
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
