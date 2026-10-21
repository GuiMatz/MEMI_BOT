# Instruções do projeto

- Projeto: FUT.AE, o módulo de futebol (estilo Mudae) do MeMi BOT. Roda dentro do
  `memi_bot.py` da raiz, que importa `src.*` daqui; `python -m src.bot` sobe só o
  módulo, para testes locais.
- Banco SQLite em `data/memi.sqlite` (fora do Git), com migrações idempotentes em
  `src/db.py`. A inicialização carrega a Enciclopédia (`src/seeds/enciclopedia.json`)
  e liga escudos e fotos locais ao banco (`src.encyclopedia.prepare_game_data`).
- Fontes: Transfermarkt Datasets, EA FC, API-Football, TheSportsDB e a Enciclopédia.
- Posições do jogo: GOL, ZAG, LD, LE, MC, MEI, PE, PD, CA.
- O banco guarda a nota bruta das fontes; o jogo usa `src/ratings.py`
  (`game_overall`, séries de raridade, regra da roleta `POOL_SQL`). Lendas valem
  90–99, Cartola 90, e Maria Sofia é a única carta 100.
- Horários mostrados às pessoas usam Brasília (`src/clock.py`). Rolls renovam a
  cada hora cheia.
- Comandos aceitam o nome do jogador; o ID só é pedido quando há homônimos
  (`src.game.resolve_player`).
- Nunca fazer merge automático de jogadores ambíguos; `src/dedupe.py` só remove
  duplicados sem conflito de dono.
- Segredos só em `.env`; nunca imprimir, registrar ou versionar chaves.
- Código simples, tipos explícitos e comentários em português quando explicam
  lógica não óbvia. Formatação com black (linha de 100).
- Testes: `python -m unittest discover -s tests` dentro de `bot-memi/`.
