# Instruções do projeto

- Projeto: Bot Memi (Mudae de futebol), fase 1: coleta de dados.
- Banco SQLite em `data/memi.sqlite`; volume estimado de aproximadamente 150 mil
  jogadores.
- Fontes, em ordem: Transfermarkt Datasets, EA FC, API-Football e TheSportsDB.
- API-Football tem cota gratuita: conferir `x-ratelimit-requests-remaining` e
  nunca exceder a cota; implementar retomada persistente.
- Para a temporada 2026/27, considerar ativo no Transfermarkt quem tem
  `last_season >= 2025` e `current_club_id` preenchido; a API-Football não é
  requisito de elegibilidade.
- Posições únicas do jogo: GOL, ZAG, LD, LE, MC, MEI, PE, PD, CA. VOL=MC,
  SA=CA; ALA precisa de revisão por ser ambígua.
- Overall inteiro entre 1 e 99. Preferir EA FC; para os demais, estimar com o
  log do valor de mercado e o peso da liga.
- Nunca fazer merge automático de jogadores ambíguos; registrar candidatos para
  revisão manual.
- Segredos só em `.env`; nunca imprimir, registrar ou versionar chaves.
- Manter código simples, funções pequenas, tipos explícitos e comentários em
  português quando ajudarem a explicar lógica não óbvia.
- Usar lotes e `--limit 100` para validar antes de cargas completas.
- Documentação funcional completa: `docs/projeto.md`.
