# Bot Memi — base de dados de futebol

Projeto independente para coletar e organizar dados de jogadores, clubes e ligas
para o futuro bot de futebol. O banco local fica em `data/memi.sqlite`; arquivos
brutos, fotos e credenciais não devem ser versionados.

## Preparação

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
interrompem a importação; jogadores sem subposição ficam sem posição de jogo
para revisão, sem inferência automática.

## EA FC

Coloque os CSVs do EA FC 24/25 em `data/raw/`. Primeiro inspecione as posições e
colunas disponíveis:

```powershell
python -m src.import_ea --show-positions
python -m src.import_ea --limit 100
```

Ou informe arquivos explicitamente com um ou mais `--input caminho.csv`. O
importador entende os nomes de colunas comuns dos exports EA/Sofifa; posições
desconhecidas impedem a gravação até serem revisadas. Em posições múltiplas
ordenadas, utiliza a primeira como posição principal e conserva a string original
para auditoria. Os arquivos EA FC 24 e EA FC 25 são fontes distintas; não são
unidos automaticamente.

## Cruzamento e overall

```powershell
python -m src.match
python -m src.overall --limit 100
```

O cruzamento compara nome, data de nascimento e clube com RapidFuzz. Candidatos
acima de 75/100 são gravados como `pending` em `player_match_candidates` e
exportados para `data/review/player_matches.csv`; nenhum merge é aplicado
automaticamente.

O overall preserva as notas EA FC, salvo ajuste manual explícito. Os demais
jogadores recebem estimativa logarítmica pelo valor de mercado (€5 mil → 30;
€100 milhões → 88) multiplicada pelo peso da liga. Pesos não configurados usam
1,0; a tabela `league_overall_weights` aceita valores de 0,5 a 1,5. Ajustes
manuais ficam em `overall_adjustments`.

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

## Fotos e relatório

```powershell
python -m src.photos --limit 100
python -m src.photos --limit 100 --thesportsdb
python -m src.report
```

O coletor baixa URLs já fornecidas pelas fontes; `--thesportsdb` habilita a
busca por nome exato para imagens ausentes e requer `THESPORTSDB_API_KEY` no
`.env`. Respostas 404 e imagens ausentes são ignoradas; erros de rede ou HTTP
inesperados são mostrados. O relatório resume fontes, posições, faixas de
overall e jogadores sem foto.

## Fontes planejadas

1. Transfermarkt Datasets (CSV compactado).
2. Datasets EA FC 24/25 baixados manualmente do Kaggle.
3. API-Football, respeitando a cota diária informada pela API.
4. TheSportsDB para fotos, escudos e estádios.

Consulte [`docs/projeto.md`](docs/projeto.md) para regras, etapas e comandos.
As instruções de trabalho para o Copilot estão em
`.github/copilot-instructions.md`.
