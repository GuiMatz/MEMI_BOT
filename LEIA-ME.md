# MeMi BOT

Bot para Discord com gêneros, mensagens em tempo real, recuperação offline, importação
histórica, níveis 1–1000, rankings, Mudae, títulos, insígnias, Top 1 rotativo, fechamento de
períodos, resumos automáticos, perfil em três páginas, imagens (cartão, level up, resumo e
wrapped), permissões por ID e backups. `mm!changelog` mostra o que mudou em cada versão.

Os testes automáticos usam dados simulados e não conectam ao Discord; a validação ao vivo
está descrita no fim deste arquivo. O perfil ainda não tem a seção One Hit Wonders
(recurso planejado; o campo só aparecerá quando existir).

## Atualizar

**Não há configuração nova**: o `token.txt`, o `config.json` e o `memi.db` continuam valendo.
Com o repositório clonado, basta:

```bat
git pull
python -m pip install -r requirements.txt
python memi_bot.py
```

Na primeira abertura o bot migra o banco sozinho e guarda uma cópia do estado anterior em
`backups/migracao_DATA_HORA/`. Quem prefere copiar arquivos: encerre o bot, faça uma cópia da
pasta e coloque os novos `memi_bot.py`, `estilo.py`, `imagens.py`, `changelog.py` e a pasta
`assets/` (mais o `memi_tray.py`, se usar a bandeja) junto do seu `memi.db` e do `token.txt`. Não
apague nem substitua o banco por um arquivo vazio.

## Instalar do zero (Windows)

1. Encerre qualquer instância antiga do bot. Não mantenha duas abertas.
2. Coloque os arquivos do bot numa pasta, junto do `memi.db` existente (se houver) e do `token.txt`.
3. Use Python 3.10 ou superior (a integração contínua testa 3.10 e 3.12).
4. Abra o terminal nessa pasta e execute:

```bat
python -m pip install -r requirements.txt
python memi_bot.py
```

O token continua no `token.txt`, sozinho, sem aspas. Nunca publique esse arquivo. Para
configurar o dono, o canal de avisos e o servidor, use o arquivo local `config.json`
copiado de `config.example.json`; ele não deve ser compartilhado. O banco e os logs
também são locais e contêm dados do servidor.

Na primeira abertura, o bot cria um backup consistente do banco em
`backups/migracao_DATA_HORA/` e aplica a migração. Ele importa automaticamente o histórico
acessível em silêncio. Isso pode demorar em servidores grandes. A conclusão fica registrada
no `memi_bot.log`, e a indicação de importação sai dos rodapés dos rankings.

`mm!scan` força uma nova leitura completa, mostra o progresso e recalcula as conquistas
sem avisos. É o mesmo que `mm!read tudo`, que também relê o histórico, incluindo o texto dos
pedidos antigos. As leituras podem ser repetidas sem duplicar eventos. Quando já houver uma
leitura em andamento, aguarde antes de executar outra.

## Permissões e intents

No Discord Developer Portal, habilite a **Message Content Intent** (obrigatória). A **Server
Members Intent** é recomendada, mas o bot funciona sem ela (veja abaixo).

O bot precisa enxergar os canais e ler o histórico. Para comandos e respostas, precisa
enviar mensagens e embeds. Anexar arquivos é necessário para o `mm!exportar` e para as imagens
(`mm!cartao`, `mm!help`, `mm!wrapped` e o canal de avisos, com level up e resumos); sem essa
permissão no canal, essas mensagens saem só em texto. Inclua os chats
dos canais de voz. Threads privadas dependem do acesso que o bot tem a elas. A listagem
de todas as threads privadas arquivadas exige a permissão de gerenciar threads; sem ela,
o bot procura as threads privadas arquivadas das quais participa.

Chats que o bot enxerga mas não pode ler (falta “Ler o histórico de mensagens”) são
**ignorados**: as mensagens deles não entram nas contagens, mas não impedem a importação nem
as conquistas. Os IDs ficam no `memi_bot.log` (uma vez a cada mudança) e o resumo do
`mm!scan` informa quantos foram pulados. Para incluí-los, conceda a permissão e rode
`mm!scan`. Falhas passageiras do Discord, ao contrário, continuam adiando a conclusão até a
próxima leitura bem-sucedida.

Quem saiu do servidor **não aparece em nenhum ranking** (`mm!musicas ios`, `mm!tagarelas`,
`mm!levels`, `mm!mudae`, posições do `mm!perfil` e do `mm!wrapped`) nem concorre aos títulos
de Top 1 e de fechamento de período. Isso depende da **Server Members Intent**. O bot a pede
por padrão; **se ela não estiver ativada no portal, ele conecta sem ela** e registra um aviso no
log (quem saiu continua aparecendo até você ativá-la em Bot > Privileged Gateway Intents e
reiniciar). O filtro só vale depois de o bot carregar a lista completa de membros, logo após
conectar. Os dados de quem saiu não são apagados: se a pessoa voltar, reaparece com o histórico.

Para nem pedir essa intent, inicie assim no Prompt de Comando:

```bat
set MEMI_MEMBERS_INTENT=0
python memi_bot.py
```

O código trabalha com um servidor, como o banco original. Ele identifica o servidor pelo
canal de avisos configurado em `config.json` ou pelo único servidor conectado. Se
necessário, defina `MEMI_GUILD_ID` com o ID do servidor antes de iniciar. Configure também
`owner_id` e `notice_channel_id` em `config.json`; sem um ID de dono, os comandos
administrativos ficam desabilitados. Não reutilize este banco para outro servidor: os
dados históricos originais não registram um ID de servidor.

## Uso em segundo plano

Depois de validar a execução no terminal:

```bat
pythonw memi_bot.py
```

No Agendador de Tarefas do Windows, configure o caminho completo de `pythonw.exe`,
o caminho completo do script como argumento e a pasta do bot em “Iniciar em”.
As variáveis de ambiente precisam estar disponíveis para essa tarefa, se você as usar.
Erros e progresso ficam em `memi_bot.log`, com rotação de três arquivos de até 2 MB.

O bot não abre duas vezes: uma segunda cópia (terminal, tarefa agendada ou tray) encerra
com uma mensagem no log. Se o processo anterior terminar de forma anormal, a trava é liberada
pelo sistema; não há arquivo para apagar.

### Ícone na bandeja do Windows

`memi_tray.py` roda o mesmo bot com um ícone na bandeja (abrir a pasta, ver o log, sair) e
pode iniciar junto com o Windows:

```bat
python -m pip install -r requirements-tray.txt
python memi_tray.py               :: roda agora
python memi_tray.py --instalar    :: abre sozinho ao ligar o Windows
python memi_tray.py --desinstalar
```

Ele usa o mesmo `memi_bot.log` e a mesma trava de instância do `memi_bot.py`.

Há um backup SQLite comprimido por dia em `backups/`, com retenção dos últimos sete.
Os backups anteriores à migração ficam em subpastas separadas e não entram nessa limpeza.

## Imagens

O bot gera imagens no visual do projeto (`imagens.py`): o banner do `mm!help`, o `mm!cartao`, o
aviso de level up, o resumo do mês/ano e o `mm!wrapped`. Elas usam o **Pillow**, que já está no
`requirements.txt`. Se ele faltar, se o bot não puder anexar arquivos no canal ou se a geração de
uma imagem falhar ou demorar mais de 15 segundos, a mensagem sai normalmente só em texto. O
`mm!cartao` é a exceção: sem Pillow ou sem permissão de anexar ele avisa e mostra o perfil comum, e
se a geração falhar ele responde que não conseguiu gerar o cartão.
As imagens pessoais usam a cor escolhida com `mm!ec` (sem escolha, o coral padrão); as do servidor
(ajuda, resumos) usam sempre o coral padrão.

As fontes ficam em `assets/fonts`: Manrope (licença OFL) e DejaVu Sans (para alfabetos que a
Manrope não cobre), com as licenças ao lado. Se elas faltarem, o texto das imagens sai sem
acentos. Emojis não são desenhados; nomes em alfabetos que nenhuma fonte cobre (japonês, árabe,
hebraico…) usam o nome de usuário, ou "Membro" se também não der.

## Aparência das mensagens

Cores e emojis do bot ficam no topo de `estilo.py` (`EMOJI`, `COR_PADRAO`, `COR_SUCESSO`,
`COR_AVISO`, `COR_ERRO`). Para usar um emoji do servidor, troque o valor por `<:nome:ID>` e
reinicie o bot. Confirmações aparecem em verde (✅), avisos em amarelo (⚠️) e erros em vermelho (❌);
o perfil usa a cor escolhida com `mm!ec`.

## Comandos

| Comando | Comportamento |
| --- | --- |
| `mm!musicas` | Faixas mais tocadas |
| `mm!musicas artista` | Artistas por quantidade de faixas diferentes |
| `mm!musicas ios` | Pessoas por pedidos de música |
| `mm!musicas genero` | Um flag como os outros: gêneros por tocadas e quantidade de faixas diferentes |
| `mm!musicas genero musica brasileira mes` | Faixas do gênero; aceita espaços e ignora acentos |
| `mm!musicas [tipo] mes/ano` | Use um período: mês atual ou ano atual (não há ranking semanal) |
| `mm!tagarelas [ios/bot] [mes/ano]` | Mensagens; sem filtro inclui pessoas e bots |
| `mm!levels` | Níveis de pessoas, por total histórico |
| `mm!mudae` | Roletadas de pessoas, por total histórico |
| `mm!hall [mes/ano]` | Vencedores (DJ e Tagarela) dos meses ou anos já fechados |
| `mm!perfil [@pessoa]` | Três páginas, botões públicos com timeout de três minutos |
| `mm!insignias [@pessoa]` | Insígnias, incluindo quantidades acumuladas |
| `mm!titulos [@pessoa]` | Todos os títulos possuídos |
| `mm!frase TEXTO` | Frase de até 100 caracteres (aparece em itálico no perfil); sem texto limpa |
| `mm!cartao [@pessoa]` | Cartão de perfil em imagem (precisa do Pillow; sem ele mostra o perfil comum) |
| `mm!ec COR` | Cor do embed do seu perfil: `#ff8800`, `ff8800` ou um nome (vermelho, laranja, amarelo, dourado, verde, ciano, azul, roxo, rosa, marrom, cinza, branco, preto); `mm!ec padrao` restaura |
| `mm!favorita MÚSICA - ARTISTA` | Salva a favorita e informa se encontrou a capa |
| `mm!titulo NOME` | Seleciona um título possuído, ignorando acentos e caixa |
| `mm!aleatoria` | Sorteia uma faixa e mostra o comando de play |
| `mm!wrapped` | Resumo dos últimos 12 meses, preservado |
| `mm!help` (ou `mm!ajuda`, `mm!comandos`) | Ajuda pública |
| `mm!changelog [versão]` (ou `mm!novidades`) | Mudanças da versão mais recente, com um menu para ver as anteriores; `mm!changelog 2.0.0` abre uma versão direto |

As barras da tabela indicam alternativas; não são digitadas. Exemplos:
`mm!tagarelas ios mes`, `mm!musicas artista ano`, `mm!musicas genero rock mes`.

Somente o ID definido em `owner_id` em `config.json` pode usar:

```text
mm!read
mm!read tudo
mm!read #canal1 #canal2
mm!scan
mm!exportar
mm!give titulo @pessoa NOME
mm!give insignia @pessoa NOME
```

`mm!exportar` envia os mesmos cinco CSVs de música do bot original. Como eles trazem IDs,
nomes e o texto dos pedidos de todos os membros, o comando é restrito ao dono (no bot
original qualquer membro podia usá-lo).

`read`, `scan`, `give` e `exportar` não aparecem na ajuda pública.
Concessões manuais não enviam aviso de desbloqueio.

## Catálogo e pendências do dono

Edite o bloco `CATALOGO`, perto do começo do código, e reinicie o bot. Os identificadores
são persistentes: mantenha-os quando trocar o nome ou o emoji de um item já concedido.
Um emoji comum pode ser substituído por `<:nome:ID>` ou `<a:nome:ID>`.

Cartola/Cartoleiro, WPlace/Pintador, BONGAS e Bréca Games estão cadastrados com os nomes
provisórios da especificação. Conceda o título e a insígnia com os respectivos comandos.
Para um troféu de Cartola que precise conceder ambos juntos, adicione uma entrada com
`titulo=True`, `insignia=True`, `manual=True` e `vinculado=True`. Nenhum troféu foi inventado.

Os 12 comandos curtos do Mudae estão em `COMANDOS_MUDAE`. Os nomes por extenso não
foram ativados porque a especificação exige confirmação no servidor. Acrescente-os
à constante depois dessa confirmação. O prefixo está em `PREFIXO_MUDAE`.

Ainda faltam os emojis definitivos, os nomes finais dos itens manuais e a lista de
troféus do Cartola. Esses dados não impedem o funcionamento dos demais recursos.

## Como os dados são tratados

- `musicas`, `tocadas`, `pedidos`, `atividade`, `deezer_cache`, `links_cache` e os antigos
  marcadores permanecem no banco. A tabela `atividade` conserva o resultado do scan antigo
  para auditoria; o ranking novo usa mensagens individuais.
- A contagem antiga agregada não tem datas nem IDs. Por isso, as mensagens são importadas
  novamente para calcular os períodos sem inventar datas nem somar duas vezes. Mensagens
  já apagadas ou inacessíveis não podem ser recuperadas.
- Mensagens comuns guardam ID, autor, canal e indicador de bot, sem o conteúdo. O texto dos
  pedidos musicais continua sendo guardado, como no bot original.
- Mensagens, pedidos, roletadas, posses e períodos fechados têm chaves únicas. Os pontos
  de progresso não avançam além de trechos de histórico confirmados.
- Uma playlist conta como um pedido. Cada aviso de faixa tocada conta como uma tocada.
  Playlists e álbuns ficam fora da música mais colocada do perfil.
- Mês e ano seguem Brasília. O fechamento começa na virada, após recuperar o histórico
  até aquele instante. Uma falha de leitura adia o fechamento; o motivo fica no log e a
  nova tentativa só ocorre depois de 15 minutos.
- Além da recuperação ao reconectar, o bot faz uma leitura de segurança a cada 15 minutos.
  Ela pula a listagem de threads arquivadas (que não recebem mensagens sem serem
  reabertas); a leitura completa acontece ao iniciar, ao reconectar e na virada do mês.
- O banco usa o modo WAL com `synchronous=NORMAL`: após uma queda de energia, as últimas
  gravações podem se perder, mas o arquivo continua íntegro e o histórico é relido do Discord.
  O backup diário roda em uma thread própria, sem travar o bot.
- Top 1 geral é rotativo. Títulos por quantidade e vitórias mensais/anuais são permanentes.
  A importação inicial é silenciosa; a recuperação posterior pode gerar os avisos pendentes.
- Deezer e oEmbed dependem de disponibilidade externa. A classificação ocorre em lotes
  pequenos; gêneros e capas desconhecidos não bloqueiam os comandos. O rodapé mostra
  quantas faixas ainda não têm gênero. O gênero favorito considera todos os pedidos de
  faixas já classificados, com peso pela frequência, e informa os que ainda faltam.
- Ao alcançar um novo marco de nível (10, 20, 30…) a pessoa ganha um aviso no canal de avisos.
  Só pessoas; a importação inicial e o `mm!scan` atualizam o marco em silêncio, e quem já tinha
  nível alto quando o bot foi atualizado não recebe aviso dos marcos antigos. Se alguém cruza
  vários marcos de uma vez (bot desligado), avisa só o mais alto.
- Quando um mês ou ano fecha com o bot ligado, ele posta sozinho um resumo no canal de avisos
  (DJ, Tagarela, música mais tocada e totais; no ano, os tops). O fechamento da importação
  inicial e do `mm!scan` é silencioso e não gera resumo, nem retroativo. Períodos sem nenhuma
  atividade de pessoas não geram resumo.
- Os avisos usam nomes e desativam menções. Para evitar duplicações após uma queda, o
  envio é reservado no banco antes da chamada ao Discord. Se houver queda exatamente
  nesse intervalo, um aviso pode faltar; a recompensa permanece salva. Falhas com
  resultado de envio incerto ficam registradas no log e não são reenviadas automaticamente.

## Testes

```bat
python -m unittest discover -v -p "test_*.py"
```

Os testes rodam sem conectar uma conta ao Discord e cobrem contagens, níveis, títulos,
períodos, migração, backups, transações, reconexão, threads arquivadas, gêneros, perfil,
permissões, a leitura que cruza a meia-noite e a manutenção (repetição após falhas). O
resultado de cada alteração é o da integração contínua no GitHub. Os avisos impressos vêm
de falhas simuladas e do bot de teste sem intents privilegiados.

Validação ao vivo que falta realizar no seu servidor:

1. Enviar uma mensagem e verificar `mm!tagarelas` e `mm!levels`.
2. Reiniciar e conferir a persistência.
3. Desligar, enviar mensagens e pedidos, religar e conferir a recuperação.
4. Executar `mm!scan` e conferir usuários com histórico conhecido, sem avisos de importação.
5. Testar os pedidos de música, uma playlist e `m!loop`.
6. Testar `$w`, `$wa`, `$h`, `$m` e conferir `mm!mudae`.
7. Conferir as três páginas, a favorita e a navegação por outra pessoa.
8. Conferir um desbloqueio no canal de avisos, sem menção.
9. Conferir que quem saiu some de todos os rankings e reaparece ao voltar (com Members Intent).
10. Conferir `mm!help`, `mm!cartao` e `mm!wrapped` com imagem, e sem Pillow (mensagem só em texto).
11. Avançar alguém para um marco de nível (10, 20…) e conferir o aviso com imagem, uma vez só.
12. Fechar um mês no servidor de teste e conferir o resumo automático com imagem.
13. `mm!changelog`: versão mais recente, menu com a anterior e `mm!changelog 2.0.0`.

A cópia do código original está em `original/memi_bot_original.py` neste repositório.
Para reverter uma migração, pare o bot e restaure juntos o código antigo e o banco do
backup anterior; não substitua um banco enquanto o bot estiver rodando.

Referência técnica consultada: [documentação oficial do discord.py](https://discordpy.readthedocs.io/en/stable/).
