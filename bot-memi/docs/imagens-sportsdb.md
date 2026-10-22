# Imagens do TheSportsDB: ligas, técnicos, estádios, lendas e times

O script `src/sportsdb_images.py` baixa do TheSportsDB as imagens que o FUT.AE ainda
não tem. Cada imagem é salva com o **ID do registro no banco**, do mesmo jeito que as
fotos dos jogadores. Assim o bot sabe a quem cada arquivo pertence sem nenhuma lista
extra.

| Tipo (`--tipos`) | Tabela | Coluna da imagem | Pasta do arquivo |
|---|---|---|---|
| `ligas` | `leagues` | `logo_path` | `data/photos/leagues/<id>.<ext>` |
| `tecnicos` | `coaches` | `photo_path` | `data/photos/coaches/<id>.<ext>` |
| `estadios` | `stadiums` | `photo_path` | `data/photos/stadiums/<id>.<ext>` |
| `lendas` | `players` (só `Lenda` e `Lenda de Clube`) | `photo_path` | `data/photos/players/<id>.<ext>` |
| `times` | `clubs` | `logo_path` | `data/photos/clubs/<id>.<ext>` |

---

## Para pessoas

### 1. Antes de começar

- Rode tudo na pasta `bot-memi`, no computador ou servidor onde fica o banco do bot.
- Coloque a chave do TheSportsDB no arquivo `.env`:
  ```
  THESPORTSDB_API_KEY=123
  ```
  `123` é a chave gratuita. Ela funciona, mas com limites (veja o passo 6).
- Não precisa desligar o bot. Mesmo assim, é mais seguro rodar com ele desligado ou
  num horário calmo, porque o script escreve no mesmo banco.

### 2. Faça um teste pequeno primeiro

```powershell
python -m src.sportsdb_images --limite 5
```

Isso busca no máximo 5 itens de cada tipo e mostra um resumo como este (os números
são só um exemplo):

```json
{
  "ligas": {"baixadas": 4, "nao_encontradas": 1},
  "tecnicos": {"baixadas": 3, "nao_encontradas": 2},
  "estadios": {"baixadas": 2, "nao_encontradas": 3},
  "lendas": {"baixadas": 3, "nao_encontradas": 2},
  "times": {"escudos_novos": 6, "times_criados": 12, "ja_tinham": 30, "ligas_sem_times": 1}
}
```

Abra a pasta `data/photos/leagues/` (e as outras): os arquivos têm o número do ID,
por exemplo `12.png`.

### 3. Rode tudo

```powershell
python -m src.sportsdb_images
```

O script faz cerca de 30 buscas por minuto, que é o limite do TheSportsDB. Com
centenas de itens, isso leva de alguns minutos a algumas horas. Pode deixar rodando.
Se você interromper (Ctrl+C), nada se perde: o que já foi baixado fica salvo.

### 4. Rode só o que você quer

```powershell
python -m src.sportsdb_images --tipos ligas
python -m src.sportsdb_images --tipos tecnicos,estadios
python -m src.sportsdb_images --tipos lendas
python -m src.sportsdb_images --tipos times
```

### 5. Veja no Discord

Reinicie o bot e use:

- `mm!scout <nome da liga, técnico, estádio ou time>`: a ficha mostra a imagem;
- `mm!cosmeticos liga`, `mm!cosmeticos tecnico`, `mm!cosmeticos estadio`: as cartas
  dos cosméticos passam a usar a imagem real em vez do ícone;
- `mm!top emblemas`: os times novos aparecem como emblemas, e eles também saem nas
  crates;
- `mm!scout <nome da lenda>`: a carta da lenda com foto.

### 6. Por que algumas imagens não vieram

- **Chave gratuita:** a lista de times de cada liga vem cortada em 10, e a busca de
  jogador traz poucos resultados. Lendas de um nome só (por exemplo "Romário") podem
  não ser achadas. Uma chave paga do TheSportsDB resolve: troque a chave no `.env` e
  rode com `--retentar`.
- **Nome diferente:** o script só aceita o nome exato, ignorando acentos e
  maiúsculas. Isso evita foto errada, mas deixa passar quem está escrito de outro
  jeito.
- **Homônimos:** se houver dois resultados possíveis, o script não baixa nenhum.

### 7. Tentar de novo quem falhou

Quem não foi encontrado fica anotado e é pulado nas próximas rodadas, para não gastar
buscas à toa. Para tentar de novo:

```powershell
python -m src.sportsdb_images --retentar
```

### 8. Colocar uma imagem na mão

Se o TheSportsDB não tem a imagem (por exemplo, jogadores inventados do Cartola),
salve o arquivo com o ID do registro na pasta certa, por exemplo
`data/photos/players/48213.png`, e reinicie o bot. Ele liga a imagem sozinho.

---

## Para IA

Contrato e limites para um agente que vá rodar, alterar ou depurar este recurso.

### Entrada e saída

- **Comando:** `python -m src.sportsdb_images [--tipos T1,T2] [--limite N] [--retentar] [--database CAMINHO] [--photo-root CAMINHO] [--api-key CHAVE]`, rodado em `bot-memi/`.
- **Tipos válidos:** `ligas`, `tecnicos`, `estadios`, `lendas`, `times`. Sem `--tipos`, roda todos nessa ordem.
- **Saída:** JSON no stdout, com contadores por tipo (`baixadas`/`nao_encontradas`; em `times`: `escudos_novos`, `times_criados`, `ja_tinham`, `ligas_sem_times`). Os logs vão para o stderr.
- **Chave:** `THESPORTSDB_API_KEY` (lida de `bot-memi/.env`) ou `--api-key`. Sem chave, sai com erro de uso.

### Regra do ID (não quebre)

- O arquivo é sempre `<photo-root>/<tabela>/<id da linha>.<ext>`, com `<ext>` vindo do Content-Type (`.png`, `.jpg`, `.webp`, `.gif`).
- A coluna de caminho guarda o caminho **relativo ao photo-root** (ex.: `leagues/12.png`). `src.roll_images.find_image` resolve esse caminho, e `src.photos.sync_existing_photos` religa os arquivos órfãos na inicialização do bot (agora inclui `coaches` e `leagues`).
- A coluna de URL (`logo_url`/`photo_url`) guarda a origem.

### Invariantes

1. **Não duplicar clubes.** A identidade de um clube é `src.game._club_key(nome)` (nome normalizado, sem FC/SC/CF…). Antes de criar, o script testa a chave de `strTeam`, de `strTeamShort` e de cada `strTeamAlternate`. Clube existente com `logo_path` preenchido nunca é sobrescrito.
2. **Só nome exato.** A comparação usa `normalize_player_name` (sem acentos, minúsculas, pontuação vira espaço). Em homônimos, filtra por nacionalidade (`src.encyclopedia.COUNTRIES_EN` converte o país do banco para o inglês) e por posição Manager/Coach (técnicos) ou status Retired (lendas). Se continuar ambíguo, **não baixa**.
3. **Não reprocessar à toa.** Cada tentativa é gravada em `image_attempts(entity, entity_id, found)`. Sem `--retentar`, registros já tentados são pulados.
4. **Respeitar o limite da API.** `SportsDB.get` espera `MIN_INTERVAL = 2.1 s` entre chamadas, aguarda 60 s quando recebe HTTP 429 e tenta até 4 vezes. Não reduza esse intervalo com a chave gratuita.
5. **Lendas** são `players.overall_source IN ('Lenda', 'Lenda de Clube')` com `photo_path IS NULL`. Apelidos vêm de `src/seeds/lendas_brasileiras.json` (`aliases`) e de `NICKNAME_SUFFIXES`.

### Endpoints usados (TheSportsDB v1)

| Uso | Endpoint | Campos lidos |
|---|---|---|
| Ligas de um país | `search_all_leagues.php?c=<país>&s=Soccer` | `countries[].strLeague`, `strLeagueAlternate`, `strBadge`, `strLogo` |
| Times de uma liga | `search_all_teams.php?l=<strLeague>` | `teams[].strTeam`, `strTeamShort`, `strTeamAlternate`, `strCountry`, `strBadge` |
| Técnicos e lendas | `searchplayers.php?p=<nome>` | `player[].strPlayer`, `strNationality`, `strPosition`, `strStatus`, `strSport`, `strCutout`/`strThumb`/`strRender` |
| Estádios | `searchvenues.php?v=<nome>` | `venues[].strVenue`, `strVenueAlternate`, `strThumb`, `strFanart1` |

Com a chave `123`, `search_all_teams` devolve no máximo 10 times e `searchplayers` traz poucos resultados (verificado em 22/10/2026).

### Onde as imagens aparecem no bot

- Catálogo de cosméticos (`src.cosmetics.catalog`): ligas usam `leagues.logo_path`; emblemas incluem `clubs.logo_path LIKE 'clubs/%'`.
- `mm!scout` (`src.game.SCOUT_TABLES`): ligas usam `logo_path`, e clubes aparecem como "Emblema".
- Cartas de jogador e escalação: `players.photo_path`.

### Como verificar uma mudança

```powershell
python -m unittest tests.test_sportsdb_images      # TheSportsDB simulado, sem rede
python -m unittest discover -s tests                # suíte inteira do FUT.AE
python -m src.sportsdb_images --limite 3 --tipos ligas   # teste real e curto (usa a API)
```

Para checar o banco depois de uma rodada:

```sql
SELECT entity, found, COUNT(*) FROM image_attempts GROUP BY entity, found;
SELECT COUNT(*) FROM clubs WHERE logo_path LIKE 'clubs/%';
SELECT name, COUNT(*) FROM clubs GROUP BY lower(name) HAVING COUNT(*) > 1;  -- deve vir vazio para os times criados pelo script
```

### O que não fazer

- Não salve imagem com outro nome que não seja o ID; o bot não acharia.
- Não apague `image_attempts` só para "forçar"; use `--retentar`.
- Não aceite resultado por similaridade aproximada sem revisão humana: foto errada é pior que nenhuma.
- Não versione `data/photos/` no Git; a pasta é local de cada instalação.
