# MeMi BOT

Bot para Discord com gêneros, mensagens em tempo real, recuperação offline, importação
histórica, níveis 1–100 com XP e patentes, rankings, Mudae tracker, tags (títulos e insígnias
com emojis próprios), Top 1 rotativo, fechamento de períodos, resumos automáticos, perfil em
três páginas, imagens (cartão, level up, resumo e wrapped), permissões por ID e backups.
`mm!changelog` mostra o que mudou em cada versão.

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
pasta e coloque todos os `.py` novos (`memi_bot.py`, `estilo.py`, `imagens.py`, `changelog.py`,
`progressao.py`, `tags.py`, `emojis.py`, `mudae.py` e, se usar a bandeja, `memi_tray.py`) e a
pasta `assets/` junto do seu `memi.db` e do `token.txt`. Não apague nem substitua o banco por um
arquivo vazio.

Ao atualizar para a 2.2, acontece sozinho, sem nenhum passo manual:

- os níveis passam para a escala de 1 a 100 (XP) sem avisos retroativos; o primeiro nível de
  cada pessoa é gravado em silêncio e só as subidas seguintes são anunciadas;
- títulos e insígnias já conquistados viram tags (título e insígnia ao mesmo tempo);
- o bot envia as insígnias e os emblemas das patentes como emojis da aplicação (veja
  "Emojis personalizados");
- o bot relê uma vez, em segundo plano, os canais onde o Mudae já falou, para montar o
  histórico do Mudae tracker. O rodapé do `mm!mudae` avisa enquanto isso.

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

O cartão usa o fundo `assets/banners/cartao.jpg` (o avatar entra na lente direita dos óculos) e
o `mm!help` usa o banner `assets/banners/ajuda.jpg`. Para trocar, substitua os arquivos mantendo
1200x400 px; se a posição das lentes mudar, ajuste `LENTE_DIREITA` em `imagens.py`. Sem esses
arquivos, o bot desenha um fundo próprio. O Discord mostra as imagens reduzidas, então nenhum
texto delas fica abaixo de 24 px.

## Aparência das mensagens

Cores e emojis do bot ficam no topo de `estilo.py` (`EMOJI`, `COR_PADRAO`, `COR_SUCESSO`,
`COR_AVISO`, `COR_ERRO`). Confirmações aparecem em verde (✅), avisos em amarelo (⚠️) e erros em
vermelho (❌); o perfil usa a cor escolhida com `mm!ec`.

### Emojis personalizados

Ao ligar, o bot envia como **emojis da aplicação** (do próprio bot, sem ocupar espaço nem pedir
permissão no servidor):

- as insígnias das tags, de `assets/insignias/` (o arquivo de cada tag está em `tags.py`);
- os emblemas das patentes: `assets/patentes/<id>.png`, se existir, ou um emblema desenhado pelo
  bot (escudo na cor da patente com o nível);
- qualquer `assets/emojis/<chave>.png`, em que `<chave>` é um nome de `EMOJI` em `estilo.py`
  (por exemplo `musica.png`, `mensagens.png`, `mudae.png`): ele substitui o emoji padrão nas
  mensagens. Rodapés e títulos de embed continuam com o emoji padrão, porque o Discord não
  desenha emojis personalizados neles.

Cada imagem só é reenviada quando o arquivo muda. Use PNG quadrado de até 256 KB (o limite do
Discord). Se o envio falhar (rede, limite), o bot segue com os emojis padrão e registra o motivo
no log. Os emojis do bot aparecem no Developer Portal, em "Emojis" do aplicativo.

## Comandos

| Comando | Comportamento |
| --- | --- |
| `mm!musicas` (`mm!msc`) | Faixas mais tocadas |
| `mm!musicas artista` | Artistas por quantidade de faixas diferentes |
| `mm!musicas ios` | Pessoas por pedidos de música |
| `mm!musicas genero` | Ranking de gêneros por tocadas e quantidade de faixas diferentes |
| `mm!musicas [tipo] mes/ano` | Use um período: mês atual ou ano atual (não há ranking semanal) |
| `mm!tagarelas [bots/todos] [mes/ano]` (`mm!tg`) | Mensagens de pessoas; `bots` mostra só os bots e `todos` mistura os dois |
| `mm!levels` (`mm!lvl`) | Nível, patente e XP de cada pessoa |
| `mm!hall [mes/ano]` (`mm!h`) | Vencedores (DJ e Resenhex) dos meses ou anos já fechados, seis por página |
| `mm!mudae` (`mm!md`) | Resumo top 5 de usuários IOS, personagens e séries |
| `mm!mudae ios` | Usuários IOS ordenados pela quantidade de rolls |
| `mm!mudae personagens` | Personagens mais roletados, contando apenas rolls ligados a comandos |
| `mm!mudae series` | Séries mais roletadas, contando apenas rolls ligados a comandos |
| `mm!perfil [@pessoa]` (`mm!p`) | Três páginas, botões públicos com timeout de três minutos |
| `mm!cartao [@pessoa]` (`mm!c`) | Cartão de perfil em imagem (precisa do Pillow; sem ele mostra o perfil comum) |
| `mm!tags [@pessoa]` (`mm!t`, `mm!titulos`, `mm!insignias`, `mm!i`) | Tags por categoria, com como ganhou e quantas vezes |
| `mm!tags todos` (`mm!th`) | Todas as tags, uma categoria por página, marcando as que você tem |
| `mm!titulo NOME` | Escolhe o título exibido (qualquer tag sua), ignorando acentos e caixa |
| `mm!frase TEXTO` (`mm!f`) | Frase de até 100 caracteres (aparece em itálico no perfil); sem texto limpa |
| `mm!favorita MÚSICA - ARTISTA` (`mm!fm`) | Salva a favorita e informa se encontrou a capa |
| `mm!ec COR` (`mm!embedcolor`) | Cor do seu perfil e das suas imagens: `#ff8800`, `ff8800` ou um nome (vermelho, laranja, amarelo, dourado, verde, ciano, azul, roxo, rosa, marrom, cinza, branco, preto); `mm!ec padrao` restaura |
| `mm!aleatoria` | Sorteia uma faixa e mostra o comando de play |
| `mm!wrapped` (`mm!w`) | Resumo dos últimos 12 meses |
| `mm!help` (ou `mm!ajuda`, `mm!comandos`) | Ajuda pública, com o banner |
| `mm!changelog [versão]` (`mm!cl`, `mm!novidades`) | Mudanças da versão mais recente, com um menu para ver as anteriores; `mm!changelog 2.1.0` abre uma versão direto |

As barras da tabela indicam alternativas; não são digitadas. Os atalhos entre parênteses não
aparecem na ajuda. Exemplos: `mm!tagarelas bots mes`, `mm!musicas artista ano`,
`mm!mudae personagens`.

Somente o ID definido em `owner_id` em `config.json` pode usar:

```text
mm!read
mm!read tudo
mm!read #canal1 #canal2
mm!scan
mm!exportar
mm!give @pessoa TAG
mm!mudaedump [#canal] [quantidade]
```

`mm!exportar` envia os mesmos cinco CSVs de música do bot original. Como eles trazem IDs,
nomes e o texto dos pedidos de todos os membros, o comando é restrito ao dono (no bot
original qualquer membro podia usá-lo).

`mm!give @pessoa TAG` concede uma tag manual (as do Cartola e da comunidade); aceita o nome atual
ou o antigo, sem acentos, e o formato antigo `mm!give titulo @pessoa NOME`. Para conceder várias de
uma vez, separe os nomes por vírgula ou ponto e vírgula, por exemplo
`mm!give @pessoa Bréca Games, Demiurgo do Clubex`. Tags automáticas (rankings, metas, patentes)
não podem ser dadas à mão. Concessões manuais não enviam aviso.

`mm!mudaedump` exporta em JSON as últimas mensagens do Mudae e os comandos `$` de um canal (300
por padrão, até 2000), dizendo como o tracker entendeu cada uma. Serve para conferir a leitura
e para enviar amostras a quem for ajustar o tracker. O arquivo traz nomes de membros: não o
publique.

`read`, `scan`, `give`, `exportar` e `mudaedump` não aparecem na ajuda pública.

## Níveis, patentes e tags

**XP** = 1 por mensagem + 25 por música pedida + ½ por roletada do Mudae, sempre calculado a
partir dos totais históricos. O nível N exige `20 × (N − 1)²` de XP: o nível 10 pede 1.620 XP,
o 50 pede 48.020 e o 100 (máximo) 196.020. Ritmo, pesos e nomes ficam em `progressao.py`.

A cada 10 níveis a pessoa sobe de **patente**: Figurante (1–9), Ouvinte da Call, Resenheiro,
Veterano da Call, Brabo da Resenha, Patrão do Clubex, Lenda Viva, Entidade, Mito do Clubex,
Divindade (90–99) e Demiurgo Supremo (100). Cada patente vira uma tag da categoria Level e pode
ser usada como título. Para renomear, troque só o `nome` em `progressao.py` (o `id` identifica a
tag já concedida).

Todo nível novo é anunciado no canal de avisos. Múltiplos de 5 e trocas de patente saem com
imagem; a troca de patente tem título e cor próprios. Se alguém sobe vários níveis de uma vez
(bot desligado), sai só o nível mais alto, e as trocas de patente do caminho também.

**Tags** juntam títulos e insígnias: cada tag é as duas coisas. O catálogo fica em `tags.py`, com
nome, categoria, "como ganhar", emoji padrão e imagem. Os identificadores são persistentes:
mantenha-os ao trocar nome, emoji ou imagem. Para criar uma tag manual nova, acrescente uma
entrada com `manual=True` e, se quiser, a imagem em `assets/insignias/`; ela passa a valer para
o `mm!give` e vira emoji sozinha na próxima vez que o bot ligar. BrécaGames e Demiurgo do Clubex
agora usam suas imagens como insígnias.

Os 12 comandos curtos de roleta do Mudae estão em `COMANDOS_MUDAE`; o prefixo está em
`PREFIXO_MUDAE`.

## Mudae tracker

O bot só lê as mensagens do Mudae; nunca interage com ele. Ele reconhece:

- **rolls**: o embed do personagem (nome, série, rank de claims/likes, valor em kakera e se está
  livre ou tem dono). O roll vai para quem usou o comando: no comando de barra, o Discord informa
  quem foi; no `$`, é o comando mais antigo ainda sem resposta no mesmo canal, nos 10 segundos
  anteriores (o Mudae responde na ordem). Recusas do Mudae ("**fulano**, a roleta está
  limitada…") consomem o comando de quem foi recusado;
- **casamentos**: "💖 **X** e **Personagem** agora são casados! 💖" (também em inglês e
  espanhol), ligados ao roll mais recente daquele personagem no canal. Casar com o roll de outra
  pessoa conta como snipe;
- **kakera**: as coletas do tipo "**fulano +401** ($k)".

Limites: o Mudae mostra as pessoas pelo nome, não pelo ID. O bot resolve o nome pelos nomes que
já viu no servidor (usuário, nome global e apelido); nome ambíguo ou de quem nunca falou fica sem
dono nas estatísticas por pessoa, mas conta nas do servidor. Kakera e ranks só aparecem se
estiverem ligados no Mudae do servidor. Textos personalizados com `$renameclaim` não são
reconhecidos. Os rankings do Mudae contam rolls reconhecidos ligados a um comando de roletar;
respostas sem vínculo com comando não entram nas listas de usuários, personagens ou séries.

Os textos em português foram escritos com base na documentação do Mudae e em bots que o leem.
Se algo não for reconhecido no seu servidor, rode `mm!mudaedump` no canal do Mudae: o resumo
mostra quantas mensagens foram entendidas como roll, casamento, kakera ou recusa e quantas não
foram reconhecidas.

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
- Ao subir de nível a pessoa ganha um aviso no canal de avisos (veja "Níveis, patentes e
  tags"). Só pessoas; a importação inicial e o `mm!scan` atualizam o nível em silêncio, e quem já
  tinha nível alto quando o bot foi atualizado não recebe aviso dos níveis antigos.
- O Mudae tracker guarda, por roll, o personagem, a série, os ranks, o valor e quem rolou; por
  casamento, quem casou, com quem e o roll ligado; por coleta, quem coletou e quanto. Também
  guarda os nomes (usuário, global e apelido) de quem fala no servidor, só para identificar as
  pessoas nas mensagens do Mudae. Ler de novo o mesmo trecho não duplica nada.
- Quando um mês ou ano fecha com o bot ligado, ele posta sozinho um resumo no canal de avisos
  (DJ, Resenhex, música mais tocada, Mudae e totais; no ano, os tops). O fechamento da importação
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

Os testes rodam sem conectar uma conta ao Discord e cobrem contagens, XP, níveis, patentes,
tags, emojis, o Mudae tracker, períodos, migração, backups, transações, reconexão, threads
arquivadas, gêneros, perfil, imagens, permissões, a leitura que cruza a meia-noite e a manutenção
(repetição após falhas). O
resultado de cada alteração é o da integração contínua no GitHub. Os avisos impressos vêm
de falhas simuladas e do bot de teste sem intents privilegiados.

Validação ao vivo que falta realizar no seu servidor:

1. Enviar uma mensagem e verificar `mm!tagarelas` e `mm!levels`.
2. Reiniciar e conferir a persistência.
3. Desligar, enviar mensagens e pedidos, religar e conferir a recuperação.
4. Executar `mm!scan` e conferir usuários com histórico conhecido, sem avisos de importação.
5. Testar os pedidos de música, uma playlist e `m!loop`.
6. Testar `$w`, `$wa`, `$h`, `$m` e conferir `mm!mudae ios`.
7. Conferir as três páginas, a favorita e a navegação por outra pessoa.
8. Conferir um desbloqueio de tag no canal de avisos, sem menção e com a insígnia como emoji.
9. Conferir que quem saiu some de todos os rankings e reaparece ao voltar (com Members Intent).
10. Conferir `mm!help`, `mm!cartao` e `mm!wrapped` com imagem, e sem Pillow (mensagem só em texto).
11. Subir alguém de nível: um nível comum sai em texto; um múltiplo de 5 e uma troca de patente
    saem com imagem, uma vez só.
12. Fechar um mês no servidor de teste e conferir o resumo automático com imagem e a seção do Mudae.
13. `mm!changelog`: versão mais recente, menu com as anteriores e `mm!changelog 2.1.0`.
14. Depois de atualizar: conferir no log "Emojis personalizados prontos" e as insígnias como
    emoji no `mm!tags`, no `mm!perfil` e no `mm!hall`.
15. Conferir `mm!levels` (nível, patente e XP) e o `mm!cartao` com o fundo novo e o título com a
    insígnia.
16. Rolar no Mudae e conferir `mm!mudae`, `mm!mudae ios`, `mm!mudae personagens` e
    `mm!mudae series`. Rodar `mm!mudaedump` no canal do Mudae e conferir que nada importante ficou
    como "não reconhecida".
17. Conferir `mm!tagarelas` (só pessoas) e `mm!tagarelas bots`, `mm!give @pessoa Papagaio da
    Call` e os atalhos (`mm!tg`, `mm!lvl`, `mm!p`, `mm!c`, `mm!cl`).

A cópia do código original está em `original/memi_bot_original.py` neste repositório.
Para reverter uma migração, pare o bot e restaure juntos o código antigo e o banco do
backup anterior; não substitua um banco enquanto o bot estiver rodando.

Referência técnica consultada: [documentação oficial do discord.py](https://discordpy.readthedocs.io/en/stable/).
