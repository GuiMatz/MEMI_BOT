# Bot Memi — base de dados de futebol

Projeto independente para coletar e organizar dados de jogadores, clubes e ligas
para o futuro bot de futebol. O banco local fica em `data/memi.sqlite`; arquivos
brutos, fotos e credenciais não devem ser versionados.

## Preparação

Para a visão do jogo, ideias de mecânicas, comandos planejados e decisões
pendentes, consulte o [guia de ideias do jogo](docs/guia-ideias-jogo.md).

No Windows, a partir desta pasta:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
python -m src.db
```

No macOS/Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
python -m src.db
```

O comando `src.db` cria o schema sem apagar dados existentes. Configure as
credenciais apenas no `.env`; nunca as envie pelo chat nem as versione.

Execute os testes do projeto com:

```powershell
python -m unittest discover -s tests -v
```

## Transfermarkt

Baixe `players.csv.gz` e `clubs.csv.gz` para `data/raw/` a partir dos links
públicos do [Transfermarkt Datasets](https://github.com/dcaribou/transfermarkt-datasets/wiki).
Confira as categorias de posição antes de importar:

```powershell
python -m src.import_tm --show-positions
python -m src.import_tm --limit 100
```

Sem `--limit`, a importação processa o arquivo completo. Os clubes são carregados
mesmo quando os jogadores estão limitados. Uma nova execução atualiza os mesmos
registros Transfermarkt em vez de duplicá-los. Subposições não reconhecidas
interrompem a importação. Quando falta subposição, usa-se posição geral apenas
como fallback (Defender→ZAG, Midfield→MC, Attack→CA, Goalkeeper→GOL); esses
registros são marcados para revisão. Categorias Missing não recebem inferência.

## EA FC

Coloque os CSVs do EA FC em `data/raw/`. Prefira a edição mais recente disponível
(atualmente FC27) e confira a data do snapshot; FC24/25/26 podem ter elencos e
notas desatualizados. Primeiro inspecione as posições disponíveis:

```powershell
python -m src.import_ea --show-positions
python -m src.import_ea --limit 100
python -m src.import_ea --input "C:\caminho\FC27_20260912.csv" --dry-run
```

Ou informe arquivos explicitamente com um ou mais `--input caminho.csv`. O
importador entende os formatos EA/Sofifa, inclusive o formato FC27, e valida
posições principais e alternativas antes de gravar. Posições desconhecidas
impedem a importação até serem revisadas. Em posições múltiplas ordenadas, usa a
primeira como posição principal. O EA FC fornece apenas overall e posição:
o clube Transfermarkt não é sobrescrito. O importador cruza por nome e data de
nascimento; sem data, usa nome, nacionalidade e idade do snapshot. Ambíguos e
casos sem dados suficientes vão para `data/review/ea_matches.csv`, sem inserção
como novos. Jogadores só do EA podem entrar como novos, exceto os de clubes ou
ligas brasileiras. A edição mais recente prevalece.
`--dry-run` executa numa cópia temporária do banco, informa cruzamentos,
ambiguidade, novos jogadores, exclusões brasileiras e correlação entre o
overall EA e a estimativa de mercado. O banco original permanece somente leitura;
o CSV de revisão da simulação vai para `data/review/ea_matches_dry_run.csv`.

## Cruzamento e overall

```powershell
python -m src.match
python -m src.overall --limit 100
```

O cruzamento exige nome compatível e data de nascimento igual; clube não é
critério de correspondência, pois os elencos EA podem estar desatualizados.
Candidatos acima de 75/100 são gravados como `pending` em
`player_match_candidates` e exportados para `data/review/player_matches.csv`;
nenhum merge é aplicado automaticamente.

O overall preserva as notas EA FC, salvo ajuste manual explícito. Os demais
jogadores recebem estimativa logarítmica pelo valor de mercado (€5 mil → 30;
€100 milhões → 88). Clube e liga não alteram a estimativa. Ajustes manuais ficam
em `overall_adjustments`.

## API-Football

Preencha `API_FOOTBALL_KEY` em `.env`. Antes de qualquer carga completa, teste
uma combinação de liga e temporada autorizada no seu plano:

```powershell
python -m src.api_football --league ID_DA_LIGA --season ANO --limit 1
```

O cliente exige o cabeçalho de saldo em cada resposta, persiste a cota diária
observada em `import_checkpoints` e interrompe a coleta ao chegar a zero.
Importa páginas de 20 jogadores e salva página/offset para retomar. Repita o
mesmo comando no dia seguinte para continuar do ponto salvo; a carga pode ser
limitada com `--limit` durante testes.

Na temporada 2026/27, o Transfermarkt considera ativo quem tem `last_season`
2025 ou posterior; clube não participa da regra. O clube continua opcional para
exibição e pode ficar vazio se não houver correspondência em `clubs.csv.gz`.
Registros sem o corte de temporada ficam com atividade `unknown`; não são automaticamente
marcados como inativos. A API-Football pode continuar fornecendo confirmação
adicional, mas não é necessária para popular o pool. Jogadores sem overall
continuam fora do pool. Esse status é uma heurística: o aviso do dataset
Transfermarkt informa que os dados vão até 6 de julho de 2026 e não cobrem
elencos de 2026/27, portanto não comprova participação atual em campo.

O repositório dos dados informa licença CC0 1.0 para o dataset. Isso não
estabelece licença para as imagens hospedadas por Transfermarkt; não baixe nem
reutilize imagens de `image_url` sem confirmar os direitos separadamente.

## Fotos e relatório

```powershell
python -m src.photos --limit 100
python -m src.photos --limit 100 --thesportsdb
python -m src.report
```

O coletor baixa URLs já fornecidas pelas fontes; `--thesportsdb` habilita a
busca por nome exato para imagens ausentes e requer `THESPORTSDB_API_KEY` no
`.env`. Respostas 404 e imagens ausentes são ignoradas; erros de rede ou HTTP
inesperados são mostrados. O relatório resume fontes, posições, faixas de overall, jogadores sem posição,
marcados para revisão, notas 87+, atividade conhecida/desconhecida, elegíveis
para roleta, tops 30 por overall e valor de mercado, clubes sem jogadores,
cruzamentos confirmados e pendentes e jogadores sem foto.

O repositório Transfermarkt declara CC0 1.0 para o dataset, mas isso não concede
por si só direitos sobre as imagens externas apontadas em `image_url`; não
baixe nem reutilize essas imagens sem confirmar licença ou autorização própria.

## Fontes planejadas

1. Transfermarkt Datasets (CSV compactado), fonte de clube e valor de mercado.
2. Dataset EA FC mais recente disponível (atualmente FC27), baixado
   manualmente; fonte exclusiva para overall e posição.
3. API-Football, respeitando a cota diária informada pela API.
4. TheSportsDB para fotos, escudos e estádios.

Consulte [`docs/projeto.md`](docs/projeto.md) para regras, etapas e comandos.
As instruções de trabalho para o Copilot estão em
`.github/copilot-instructions.md`.
