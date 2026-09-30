# MeMi BOT 2

Atualização baseada no `memi_bot.py` enviado e na especificação `MEMIBOT_MEGA_PATCH(1).txt`.

Implementados: gêneros, mensagens em tempo real, recuperação offline, importação histórica,
níveis 1–1000, rankings, Mudae, títulos, insígnias, Top 1 rotativo, fechamento de períodos,
preferências, perfil em três páginas, permissões por ID e backups.

**35 testes automáticos passaram.** O banco real, o token e o servidor não foram fornecidos;
os testes de integração usam dados simulados. A validação no Discord e a ativação no PC
continuam pendentes. One Hit Wonder permanece para uma entrega futura, conforme solicitado.

## Atualizar no Windows

1. Encerre a versão antiga do bot. Não mantenha duas instâncias abertas.
2. Copie a pasta atual do bot para uma pasta de backup com data, antes de substituir arquivos.
   Preserve especialmente o `memi.db`, o `memi_bot.py` antigo e o `token.txt`.
3. Coloque o novo `memi_bot.py` na mesma pasta do seu **memi.db existente** e do `token.txt`.
   Não apague nem substitua o banco por um arquivo vazio.
4. Use Python 3.10 ou superior. Esta entrega foi testada em Python 3.12.14.
5. Abra o terminal nessa pasta e execute:

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
sem avisos. `mm!read tudo` também relê o histórico, incluindo o texto dos pedidos antigos.
As leituras podem ser repetidas sem duplicar eventos. Quando já houver uma leitura em
andamento, aguarde antes de executar outra.

## Permissões e intents

No Discord Developer Portal, habilite **Message Content Intent**.

O bot precisa enxergar os canais e ler o histórico. Para comandos e respostas, precisa
enviar mensagens e embeds; `mm!exportar` também precisa anexar arquivos. Inclua os chats
dos canais de voz. Threads privadas dependem do acesso que o bot tem a elas. A listagem
de todas as threads privadas arquivadas exige a permissão de gerenciar threads; sem ela,
o bot procura as threads privadas arquivadas das quais participa.

Para esconder dos rankings quem saiu, habilite **Server Members Intent** no portal e
inicie assim no Prompt de Comando:

```bat
set MEMI_MEMBERS_INTENT=1
python memi_bot.py
```

Sem essa opção, os dados dos antigos membros continuam aparecendo. Ao ativá-la, o filtro
é aplicado depois de carregar a lista completa de membros; os dados não são apagados.

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

Há um backup SQLite comprimido por dia em `backups/`, com retenção dos últimos sete.
Os backups anteriores à migração ficam em subpastas separadas e não entram nessa limpeza.

## Comandos

| Comando | Comportamento |
| --- | --- |
| `mm!musicas` | Faixas mais tocadas |
| `mm!musicas artista` | Artistas por quantidade de faixas diferentes |
| `mm!musicas ios` | Pessoas por pedidos de música |
| `mm!musicas genero` | Gêneros por tocadas e quantidade de faixas diferentes |
| `mm!musicas genero musica brasileira mes` | Faixas do gênero; aceita espaços e ignora acentos |
| `mm!musicas [tipo] semana/mes/ano` | Use um período: últimos 7 dias, mês atual ou ano atual |
| `mm!tagarelas [ios/bot] [mes/ano]` | Mensagens; sem filtro inclui pessoas e bots |
| `mm!levels` | Níveis de pessoas, por total histórico |
| `mm!mudae` | Roletadas de pessoas, por total histórico |
| `mm!perfil [@pessoa]` | Três páginas, botões públicos com timeout de três minutos |
| `mm!insignias [@pessoa]` | Insígnias, incluindo quantidades acumuladas |
| `mm!titulos [@pessoa]` | Todos os títulos possuídos |
| `mm!frase TEXTO` | Frase de até 100 caracteres; sem texto limpa |
| `mm!favorita MÚSICA - ARTISTA` | Salva a favorita e informa se encontrou a capa |
| `mm!titulo NOME` | Seleciona um título possuído, ignorando acentos e caixa |
| `mm!aleatoria` | Sorteia uma faixa e mostra o comando de play |
| `mm!wrapped` | Resumo dos últimos 12 meses, preservado |
| `mm!exportar` | Os mesmos cinco CSVs de música do bot original; permissão preservada |
| `mm!help` | Ajuda pública |

As barras da tabela indicam alternativas; não são digitadas. Exemplos:
`mm!tagarelas ios mes`, `mm!musicas artista ano`, `mm!musicas genero rock semana`.

Somente o ID definido em `owner_id` em `config.json` pode usar:

```text
mm!read
mm!read tudo
mm!read #canal1 #canal2
mm!scan
mm!give titulo @pessoa NOME
mm!give insignia @pessoa NOME
```

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
  até aquele instante. Uma falha de leitura adia o fechamento; o motivo fica no log.
- Top 1 geral é rotativo. Títulos por quantidade e vitórias mensais/anuais são permanentes.
  A importação inicial é silenciosa; a recuperação posterior pode gerar os avisos pendentes.
- Deezer e oEmbed dependem de disponibilidade externa. A classificação ocorre em lotes
  pequenos; gêneros e capas desconhecidos não bloqueiam os comandos. O rodapé mostra
  quantas faixas ainda não têm gênero. O gênero favorito considera todos os pedidos de
  faixas já classificados, com peso pela frequência, e informa os que ainda faltam.
- Os avisos usam nomes e desativam menções. Para evitar duplicações após uma queda, o
  envio é reservado no banco antes da chamada ao Discord. Se houver queda exatamente
  nesse intervalo, um aviso pode faltar; a recompensa permanece salva. Falhas com
  resultado de envio incerto ficam registradas no log e não são reenviadas automaticamente.

## Testes

```bat
python -m unittest -v test_memi_bot.py
```

O resultado desta entrega está em `RESULTADO_TESTES.txt`. Foram executados 35 testes com
Python 3.12.14, discord.py 2.7.1 e aiohttp 3.14.3, sem conectar uma conta ao Discord.
Cobrem os 15 cenários obrigatórios da especificação e também migração, backups, transações,
reconexão, threads arquivadas, gêneros, perfil, permissões e a leitura que cruza a meia-noite.
Os avisos no relatório vêm de falhas simuladas e do bot de teste sem intents privilegiados.

Validação ao vivo que falta realizar no seu servidor:

1. Enviar uma mensagem e verificar `mm!tagarelas` e `mm!levels`.
2. Reiniciar e conferir a persistência.
3. Desligar, enviar mensagens e pedidos, religar e conferir a recuperação.
4. Executar `mm!scan` e conferir usuários com histórico conhecido, sem avisos de importação.
5. Testar os pedidos de música, uma playlist e `m!loop`.
6. Testar `$w`, `$wa`, `$h`, `$m` e conferir `mm!mudae`.
7. Conferir as três páginas, a favorita e a navegação por outra pessoa.
8. Conferir um desbloqueio no canal de avisos, sem menção.
9. Se ativou Members Intent, conferir a saída e o retorno de alguém nos rankings.

A cópia do código original enviado está em `original/memi_bot_original.py` no pacote.
Para reverter uma migração, pare o bot e restaure juntos o código antigo e o banco do
backup anterior; não substitua um banco enquanto o bot estiver rodando.

Referência técnica consultada: [documentação oficial do discord.py](https://discordpy.readthedocs.io/en/stable/).
