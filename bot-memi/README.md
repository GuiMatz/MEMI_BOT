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

## Bot Discord

Preencha `DISCORD_BOT_TOKEN` no `.env` e habilite **Message Content Intent**
em Discord Developer Portal → Bot → Privileged Gateway Intents. Inicie o bot
com:

```powershell
python -m src.bot
```

O bot carrega os comandos de jogo e gerenciamento em `src/cogs/`. `mm!roll`
oferece 10 rolagens por ciclo de 30 minutos, renovadas na hora cheia e na meia hora
(horário de Brasília), e segue as faixas de raridade do
[guia de implementação](docs/guia-ideias-jogo.md). Upgrades, torre e crates podem
aumentar os rolls. Duplicatas próprias dão uma bola (até 10), +1 overall efetivo e
100 Memilets; acima do limite dão 500 Memilets. Duplicatas de terceiros oferecem de
100 a 300 Memilets a quem rolou, após resgate no botão. Jogadores livres são
reivindicados pelo botão: quem rolou tem 5 segundos de exclusividade (aumentável até
15), quem tem wish no jogador pode ter segundos de proteção, as ofertas expiram em 5
minutos e cada pessoa tem um claim por ciclo (renova junto com os rolls).
Os claims são atômicos e cada jogador só pode ter um dono.

Comandos oficiais do **FUT.AE** incluem `mm!futae`, `mm!left`, `mm!top [categoria]`, `mm!scout <nome>`, `mm!daily`, `mm!upgrade`, `mm!crates`,
`mm!wish`, `mm!wishlist`/`mm!wl`, `mm!starwish`/`mm!sw`, `mm!demitir`, `mm!troca`, `mm!time`,
`mm!escalar`, `mm!tatica`, `mm!amistoso` e `mm!campeonato`. A economia diária
usa cooldown de 24 horas; wishes e Star Wishes afetam a roleta e
os claims conforme o guia. Times têm dez formações, três estilos e imagens de
escalação PNG. As imagens locais opcionais devem ficar em
`data/assets/brasfoot/`, pasta ignorada pelo Git. O motor simula 90 minutos sem
substituições, com fórmula própria documentada no guia; campeonatos são todos
contra todos, em sequência, seguidos de final entre os dois primeiros.

### Coleta contínua de fotos

O worker abaixo busca até 30 fotos por minuto no TheSportsDB e grava os arquivos
em `data/photos/`, que o `mm!roll` usa automaticamente:

```powershell
$env:THESPORTSDB_API_KEY = "123"
python -m src.photo_worker
```

O progresso fica em `data/photo_worker_checkpoint.json`. Se o processo for
interrompido, a próxima execução continua depois do último lote sem repetir os
jogadores já processados. Falhas temporárias de rede, HTTP ou SQLite fazem o
worker aguardar e tentar o mesmo lote novamente automaticamente, com espera
progressiva de 15 segundos até no máximo 5 minutos. O processo só encerra para
erros de configuração ou entrada inválida. Para alterar a espera inicial:

```powershell
python -m src.photo_worker --retry-delay 30
```

Para testar somente um lote:

```powershell
python -m src.photo_worker --once
```

O uso da chave `123` deve respeitar os limites do TheSportsDB. O worker não
envia as fotos pelo Discord: ele salva localmente e o bot as anexa somente
quando aquele jogador for sorteado.

### Imagens de ligas, técnicos, estádios, lendas e times

Tutorial completo (para pessoas e para IA): [docs/imagens-sportsdb.md](docs/imagens-sportsdb.md).

`src.sportsdb_images` busca no TheSportsDB o que o `photo_worker` não cobre. Cada
imagem é salva com o ID do registro, igual às fotos dos jogadores
(`data/photos/leagues/<id>.png`, `coaches/`, `stadiums/`, `players/`, `clubs/`), e o
caminho fica gravado no banco:

```powershell
python -m src.sportsdb_images                          # tudo
python -m src.sportsdb_images --tipos ligas,lendas      # só alguns tipos
python -m src.sportsdb_images --limite 10               # teste rápido
python -m src.sportsdb_images --retentar                # busca de novo quem falhou
```

- **ligas**: lista as ligas de futebol de cada país e compara pelo nome e pelos nomes
  alternativos (a liga "Premier League" acha a "English Premier League");
- **técnicos**: busca pelo nome e prefere o registro com posição Manager/Coach;
- **estádios**: tenta o nome e o nome oficial ("Estádio do …"), ignorando
  "Estádio/Stadium/do" na comparação;
- **lendas**: jogadores `Lenda`/`Lenda de Clube` sem foto, pelo nome, pelos apelidos do
  arquivo das lendas e sem o sufixo de apelido ("Ronaldo Fenômeno" → "Ronaldo"),
  conferindo a nacionalidade;
- **times**: lista os times de cada liga encontrada. Time que já existe no bot (mesmo
  nome, ignorando FC/SC e acentos, ou um nome alternativo) só ganha o escudo se não
  tiver; time novo é criado com o escudo e passa a valer como emblema. Rodar de novo
  não duplica nada.

O script respeita ~30 consultas por minuto. Quem não é encontrado fica em
`image_attempts` e não é buscado de novo sem `--retentar`. Com a chave gratuita `123`,
o TheSportsDB corta as listas (5 ligas por país, 10 times por liga) e devolve poucas
respostas por busca de jogador. Por isso as ligas são casadas com o índice
`src/seeds/sportsdb_ligas.json` (todas as ligas de futebol, montado por
`python -m scripts.indexar_ligas_sportsdb`) e os times são buscados pelo ID da liga,
que traz a lista inteira. Só é aceita resposta com o nome exato (sem acentos): na
dúvida entre homônimos, o script não baixa nada.

Para testar manualmente, complete um time com `mm!time`, `mm!tatica` e
`mm!escalar <id>` (o ID aparece em `mm!elenco`), depois use
`mm!amistoso @pessoa`. Em um servidor, times completos podem se inscrever com
`mm!campeonato inscrever`; alguém com Gerenciar servidor pode iniciar com
`mm!campeonato iniciar`. O agendador automático das 19:00 em Brasília fica
desligado por padrão. Para habilitá-lo, configure o canal com
`mm!campeonato canal [#canal]`, defina `MEMI_CHAMPIONSHIP_SCHEDULER=1` no `.env`,
reinicie o bot e ative com `mm!campeonato ativar`.

O schema v11 adiciona economia, wishes, times, escalações, trocas e campeonatos
sem apagar os dados anteriores. Contas novas começam com saldo zero.

A demissão exige confirmação por botão. Uma troca só é concluída quando a
oferta do primeiro lado e a aceitação do segundo lado estão registradas.
Os comandos `mm!admin testeamistoso` e `mm!admin testecampeonato <quantidade>`
são exclusivos do proprietário configurado em `MEMI_OWNER_ID` e servem para
testar a simulação sem alterar times reais.

## Changelog completo do FUT.AE

O FUT.AE é um módulo do MeMi BOT, não um bot separado. O menu geral continua em
`mm!ajuda`; `mm!fut.ae` e `mm!futae` mostram apenas os comandos do jogo.

### 3.3 — V2: loja, crates, Centro de Treinamento e cosméticos

- **Bônus da conta** (`src/perks.py`): uma só função, `load_perks`, soma loja,
  torre, prêmios permanentes de crates (`user_bonuses`) e cosméticos equipados, e dá
  capacidade de rolls, slots de wish/Star Wish, cooldown e teto do daily, proteção
  de wish, preço da crate, renda e bilheteria. Nada disso altera a chance da roleta.
- **Loja V2** (`mm!upgrade`): 10 upgrades com limite, ícone redondo de cor própria e
  confirmação antes da compra. A redução do preço da crate não é vendida: só sai em
  crate. Migração única (versão 13 do schema): o `daily` antigo (+100 no teto) virou
  `daily_max`, o bônus de duplicata foi removido e níveis acima dos limites novos
  foram devolvidos em Memilets.
- **Crates V2** (`open_crate`): sempre um item Básico (1–3 rolls instantâneos, que
  valem só no ciclo atual, ou 150–300 Memilets) e, com chance baixa, um item de outra
  raridade: Incomum 10%, Raro 3,5%, Épico 1,2%, Lendário 0,4%. A caixa vem toda em
  itens ou toda em Memilets. Cosméticos nunca repetem o que a pessoa já tem; 5% das
  caixas com item trazem um segundo item da mesma raridade.
- **Cosméticos** (`src/cosmetics.py`, tabela `user_cosmetics`): emblema, uniforme,
  estádio, técnico, liga e mascote. Só dá para equipar o que se possui; cada um
  equipado (menos o estádio) soma +100 ao teto do daily. Itens que já estavam
  equipados antes da regra viram posse da pessoa na inicialização. Clubes e logos
  aparecem juntos como **emblemas**. Uniformes de `camisas2`/`camisas3` ganham
  "(Uniforme 2/3)" no nome.
- **Centro de Treinamento** (`src/tower.py`): 12 andares em qualquer ordem, 1000
  Memilets o primeiro e +500 a cada andar construído. Completar os 12 libera uma
  construção nova, que repete os efeitos. `mm!destruir` devolve tudo, inclusive
  retirando as bolas do andar 12.
- **Renda e bilheteria** (`src/income.py`): renda do primeiro amistoso do dia em casa
  com estádio (100 + 50 por gol, ±100 por vitória/derrota, zero se perder por 3+ sem
  marcar), com intervalo fixo de 24 h. Andar 5: cada jogo do campeonato guarda 50 (100
  na vitória) até o limite. O campeão do dia recebe 500 Memilets.
- **Lendas dos 12 grandes** (`src/seeds/lendas_brasileiras.json`,
  `seed_club_legends`): 132 nomes; quem já existe como lenda da Enciclopédia (mesmo
  nome ou apelido e mesmo país) não é duplicado, aposentados já no banco viram a carta,
  e homônimos ganham o clube no nome, como "Pepe (Santos)". Overall de 83 a 89 com
  `overall_source = 'Lenda de Clube'`.
- **Emojis** (`src/icons.py`, `scripts/gerar_icones.py`): moeda Memilets (gato de
  óculos) e ícones dos upgrades em `assets/emojis/futae/`, enviados como emojis da
  aplicação pelo MeMi BOT.
- **Escalação**: emblema embaixo à esquerda e técnico embaixo à direita.

### 3.2 — enciclopédia, cartas e roleta maior

- **Enciclopédia** (`src/encyclopedia.py`, dados em `src/seeds/enciclopedia.json`):
  carregada a cada inicialização, sem duplicar. As lendas viram cartas com
  `overall_source = 'Lenda'` (90–99, com os maiores nomes até 99). Se o jogador já
  existe no banco com o mesmo nome e nacionalidade, ele é promovido em vez de
  duplicado. Nomes de uma palavra só ("Marcos", "Juan") só promovem jogadores com
  nota 80 ou mais; abaixo disso a lenda ganha carta própria, chamada "Nome (Lenda)".
  Carga manual: `python -m src.encyclopedia`.
- **Roleta** (`POOL_SQL` em `src/ratings.py`): entra todo jogador que não está
  marcado como inativo e tem posição. Atividade `unknown` e a marca de fase 2 deixaram
  de excluir. Quem não tem overall usa nota bruta 40 (60 no jogo). Sem posição
  continua fora. `scripts/gerar_tabela_fora_roleta.py` usa a mesma regra.
- **Horários**: rolls renovam a cada hora cheia, e mensagens mostram horário de
  Brasília (`src/clock.py`, UTC−3 fixo, sem depender de `tzdata`).
- **Cartas** (`src/card_image.py`): carta vertical usada em `mm!elenco` (uma por
  página), `mm!scout` e `mm!roll`.
- **Nomes em vez de IDs** (`src.game.resolve_player`): ordem de busca é ID, nome
  exato, apelido contido no nome e iniciais. Com homônimos, abre um menu.
- **Logos**: `sync_club_logos` liga escudos e logos do Cartola aos clubes e cria
  os que faltam. Os nomes são padronizados com acentos dos clubes brasileiros.

### 3.1 — escalação clicável, jogos narrados e overall novo

- **Overall do jogo** (`src/ratings.py`): o banco guarda a nota da fonte e o jogo usa
  uma escala valorizada, `nota + (90 − nota) × 0,4` abaixo de 90 (45 → 63, 60 → 72,
  75 → 81). Jogadores Cartola valem 90 e Maria Sofia é a única carta 100; ninguém mais
  passa de 99, nem com bolas. A conta é feita por funções SQLite registradas em
  `connect()`, então reimportações não precisam de migração.
- **Séries de raridade**: Base, Comum, Incomum, Raro, Épico, Lendário, Ícone,
  Galáctico e Única (só a carta 100, com peso próprio no roll). `mm!left` mostra
  livres/total por série, além de Cartola, uniformes e logos.
- **Escalação**: `mm!escalar` sem argumentos abre um montador com menu de formação,
  menu de posição e menu de jogador filtrado pela posição e ordenado por overall, mais
  o botão **Escalar automático**. A imagem é desenhada por formação, com fotos.
- **Partidas**: o motor simula minuto a minuto (chances, gols, defesas, trave,
  cartões, intervalo, acréscimos) e a narração edita o embed lance a lance. Ajuste a
  velocidade com `MEMI_NARRATION_DELAY` (segundos, padrão 1,6). Final empatada vai para
  os pênaltis, cobrança a cobrança.
- **Economia**: crates com retorno mínimo de 350 Memilets, jackpot, reset de claim e
  garantia de prêmio permanente a cada 50; `mm!carteira` em embed; novo `mm!tu`.
- **Scout**: foto do jogador e busca em técnicos, estádios, clubes, ligas, uniformes,
  emblemas, logos e mascotes. Fotos já baixadas em `data/photos/` são ligadas ao banco
  na inicialização.
- **Duplicados**: `python -m src.dedupe` lista e `--apply` remove; o proprietário também
  pode usar `mm!admin duplicados`. Mantém o de maior overall e transfere posse, bolas,
  wishes e fontes. Grupos com donos diferentes não são alterados.

### Coleção e rolagens

- Banco local com jogadores, ligas, clubes, técnicos, estádios e fontes de dados.
- `mm!roll` com ciclos de 1 hora, raridades, claims, exclusividade temporária,
  duplicatas, bolas e recompensas.
- Jogadores sem clube aparecem como `Passes Livres`.
- Fotos locais são opcionais, têm fallback visual e são coletadas pelo worker do
  TheSportsDB sem enviar os arquivos ao Discord.
- Maria Sofia é a carta especial de overall 100; Messi e Cristiano Ronaldo ficam
  com overall 99.

### Pesquisa e catálogo

- `mm!scout <nome>` substitui `mm!im` e mostra apenas nome, overall e posição.
- Em nomes repetidos, o resultado informa série, overall, posição e ID para seleção
  inequívoca.
- IDs continuam disponíveis para wishes, elenco e trocas, mas não são a única forma
  de localizar um jogador.
- `mm!top` possui páginas e categorias de jogadores, técnicos, uniformes, mascotes,
  estádios, ligas, Cartola e emblemas.
- Jogadores são ordenados por overall; os demais itens são ordenados alfabeticamente.
- `TOP CARTOLA` reúne jogadores Cartola e os demais itens da série.

### Economia e coleção pessoal

- `mm!daily` paga de 100 a 300 Memilets (mais os bônus) e libera de novo 24 horas
  depois, menos as reduções de upgrades, torre e crates (mínimo de 8 horas).
- `mm!wishlist` e `mm!wl` mostram a lista completa de wishes em embed.
- `mm!starwish` e `mm!sw` identificam Star Wishes com o emoji `⭐`.
- `mm!upgrade` tem 10 upgrades com limite (rolls, wishes, Star Wish, daily, proteção
  de wish, teto do daily, exclusividade, reembolso da demissão, bilheteria e renda), sempre
  com confirmação.
- `mm!crates`, `mm!construir`, `mm!destruir`, `mm!renda`, `mm!bilheteria` e
  `mm!cosmeticos` (com `mm!emblema`, `mm!uniforme`, `mm!estadio`, `mm!tecnico`,
  `mm!liga` e `mm!mascote`) completam a economia do clube.
- `mm!demitir` exige confirmação antes de liberar um jogador.
- `mm!troca` exige confirmação do ofertante e do destinatário antes da conclusão.

### Times e partidas

- `mm!time`, `mm!escalar` e `mm!tatica` gerenciam time, escalação, formação e estilo.
- `mm!amistoso` simula partidas de 90 minutos com força por setores, xG, gols,
  artilheiros e estilos táticos.
- `mm!campeonato` permite inscrição, início, configuração de canal e agendamento,
  com liga, classificação e final.
- `mm!admin testeamistoso` e `mm!admin testecampeonato <quantidade>` executam testes
  com times fictícios e são restritos ao proprietário do bot.

### Operação e segurança

- Schema v11 com migrações idempotentes e preservação dos dados existentes.
- O SQLite aguarda locks temporários para permitir o bot e o worker de fotos operarem
  juntos sem falha imediata.
- Fotos, bancos, credenciais e assets locais ficam fora do Git.
- A suíte automatizada cobre banco, importadores, rolagens, economia, transações,
  partidas, campeonatos e registro dos comandos.

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
ligas brasileiras. Futebol feminino é excluído por padrão; use `--include-women`
somente se quiser incluí-lo. A edição mais recente prevalece.
`--dry-run` executa numa cópia temporária do banco, informa cruzamentos,
ambiguidade, novos jogadores, exclusões brasileiras e correlação entre o
overall EA e a estimativa de mercado. O banco original permanece somente leitura;
no ensaio, aliases de nome só são considerados com data de nascimento igual.
Aliases únicos fortes podem casar; candidatos fracos ou múltiplos são enviados
para revisão e não entram como novos. O relatório mostra os casamentos por alias,
os 30 maiores overalls que permaneceriam como novos e o CSV de revisão da
simulação em `data/review/ea_matches_dry_run.csv`. A importação normal não usa
casamento por alias.

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
