"""As frases do comentarista (`src.commentary`), separadas da lógica para editar à vontade.

Marcadores das situações: {winner} e {loser} (vencedor e perdedor; no empate no fim,
{team} é quem empatou e {other} quem cedeu), {home}, {away}, {player}, {goals},
{keeper} e {score} ("2 × 1"). As situações mais comuns têm mais frases: a análise sai
em toda partida e não pode ficar repetitiva.
"""

PHRASES: dict[str, tuple[str, ...]] = {
    # Vitória por 1 gol, sem nada mais marcante (~21% dos jogos).
    "jogo_dificil": (
        "Vitória suada do {winner}! Jogo pegado, decidido nos detalhes.",
        "Não foi bonito, mas foi eficiente. O {winner} sofreu e segurou o resultado.",
        "Três pontos com muito suor. O {loser} vendeu caro a derrota.",
        "Jogo de xadrez, e quem deu o xeque-mate foi o {winner}. Placar magro, vitória gorda.",
        "O {winner} venceu no detalhe. Num jogo desses, um lance separa a festa da frustração.",
        "Ninguém sobrou em campo. O {winner} teve um pouco mais de competência e levou.",
        "Vitória de time cascudo: o {winner} soube sofrer e soube matar o jogo.",
        "O {loser} brigou até o fim, mas o placar não andou. Mérito do {winner}.",
        "Unha roída até o apito final. O {winner} respira aliviado com o {score}.",
        "Foi na raça. O {winner} não brilhou, mas saiu com o resultado debaixo do braço.",
    ),
    # Vitória por 2 ou 3 gols (~15%).
    "vitoria_facil": (
        "Vitória tranquila do {winner}, sem sustos. Fez o dever de casa com sobras.",
        "O {winner} controlou o jogo e venceu sem precisar acelerar muito.",
        "Resultado seguro do {winner}. O {loser} até tentou, mas faltou poder de fogo.",
        "Passeio do {winner}. O {score} até ficou barato para o {loser}.",
        "O {winner} fez o simples e fez bem: {score} sem passar aperto.",
        "Jogo resolvido cedo. Depois disso, o {winner} só administrou.",
        "Superioridade clara do {winner}. O {loser} não encontrou o caminho.",
        "Atuação madura do {winner}, que venceu sem precisar forçar.",
        "O {loser} assistiu ao {winner} jogar. Vitória com autoridade.",
        "Três pontos com tranquilidade. O {winner} nem precisou tirar o pé do freio de mão.",
    ),
    # Empate decidido nos pênaltis (~12% quando o jogo vai para a disputa).
    "penaltis": (
        "Noventa minutos não bastaram. Nos pênaltis, o {winner} teve mais frieza e o {loser} "
        "vai dormir pensando na cobrança perdida.",
        "Decisão na marca da cal é loteria? O {winner} vai dizer que foi treino. Classificação "
        "suada.",
        "Coração na boca até a última cobrança. O {winner} sobreviveu; o {loser} saiu de cabeça "
        "erguida.",
        "Nos pênaltis, nervos de aço. O {winner} teve; o {loser}, nem tanto.",
        "Ninguém se entendeu com a bola rolando. Na marca do pênalti, deu {winner}.",
        "A disputa foi cruel com o {loser}. O {winner} comemora como título.",
        "Quem tem goleiro e batedor frio vai longe. O {winner} teve os dois.",
        "Empate no tempo normal, drama nos pênaltis e festa do {winner} no final.",
        "Foi no sufoco, foi nos pênaltis, mas foi do {winner}.",
        "O {loser} fez um jogo digno e caiu na loteria das cobranças. O {winner} agradece.",
    ),
    # Saiu atrás e virou (~11%).
    "virada": (
        "Começou mal, terminou sorrindo. O {winner} buscou a virada com personalidade.",
        "O {loser} saiu na frente, mas não soube segurar. Virada merecida do {winner}.",
        "Time grande se mede na adversidade: o {winner} tomou o gol e foi buscar o resultado.",
        "Tomou o gol, não se abateu e virou. Grande reação do {winner}!",
        "O {loser} achou que estava resolvido. O {winner} tinha outros planos.",
        "Virada com sabor especial: o {winner} mostrou que não se entrega.",
        "Quem saiu na frente foi o {loser}; quem sai de campo sorrindo é o {winner}.",
        "O gol sofrido acordou o {winner}. Depois dele, foi outro jogo.",
        "Reação de campeão! O {winner} virou e calou quem já dava o jogo como perdido.",
        "O {loser} vai rever o jogo pensando onde deixou escapar. Virada do {winner}.",
    ),
    # Vitória por 2+ com 60%+ de posse (~10%).
    "dominio": (
        "Domínio do começo ao fim. O {winner} teve a bola, as chances e o resultado.",
        "O {winner} jogou no campo do {loser} a partida inteira. Vitória incontestável.",
        "Foi um monólogo. O {winner} falou, o {loser} só escutou.",
        "Posse, volume e gols: o {winner} fez tudo o que um time grande precisa fazer.",
        "O {loser} correu atrás da bola os noventa minutos. Não alcançou.",
        "Aula de controle de jogo do {winner}. O {score} traduz bem o que se viu.",
        "O {winner} mandou no meio-campo e o resto foi consequência.",
        "Superioridade total: o {winner} rodou a bola, achou os espaços e matou o jogo.",
        "O {loser} mal passou do meio de campo. Dia de gala do {winner}.",
        "Bola no pé, paciência e eficiência. O {winner} jogou como quis.",
    ),
    # Empate com gols, sem nada mais marcante (~6%).
    "empate": (
        "Empate que fica de bom tamanho. Os dois tiveram seus momentos e dividiram os pontos.",
        "Ninguém mereceu perder. Jogo equilibrado, placar justo.",
        "Ficou no empate, mas teve emoção. {home} e {away} saem com um pontinho cada.",
        "Jogo de igual para igual. O {score} conta bem a história.",
        "Cada um teve o seu momento, e o placar respeitou isso. Empate justo.",
        "{home} e {away} se estudaram, se atacaram e se anularam. Divisão de pontos.",
        "Faltou alguém para desequilibrar. Fica o empate e a sensação de que dava para mais.",
        "Empate com sabor amargo para os dois: ambos acharam que podiam vencer.",
    ),
    # Venceu por 4+ gols (~5%).
    "goleada": (
        "Atropelo! O {winner} passou por cima do {loser} sem pedir licença: {score}.",
        "Foi treino de luxo para o {winner}. O {loser} precisa conversar muito no vestiário.",
        "Massacre. O {winner} fez o que quis do primeiro ao último minuto.",
        "Baile! O {winner} deu show e o placar de {score} ainda ficou barato.",
        "O {loser} vai querer esquecer esse jogo. O {winner} não vai esquecer nunca.",
        "Goleada histórica. O {winner} encontrou o caminho do gol e não largou mais.",
        "Chocolate! O {winner} aplicou {score} e saiu aplaudido.",
        "Placar de videogame: o {winner} meteu {score} e mandou um recado para todo mundo.",
    ),
    # Um goleiro segurou o jogo: até 1 gol e 3+ defesas dele (~3%).
    "goleiro": (
        "{keeper} fechou o gol! Sem ele, o resultado seria outro.",
        "Noite de paredão: {keeper} pegou tudo o que chegou.",
        "Esse jogo tem dono, e ele usa luvas. Atuação monstruosa de {keeper}.",
        "{keeper} parecia ter quatro braços. Os atacantes vão sonhar com ele.",
        "Quando o goleiro é o melhor em campo, alguém não fez o seu trabalho. {keeper} fez.",
        "Pegou até pensamento. São {keeper}!",
    ),
    # Empatou já nos minutos finais (~3%).
    "empate_fim": (
        "Quando parecia acabado, o {team} arrancou o empate no fim. Futebol é isso!",
        "O {other} já contava os três pontos e viu o empate escapar nos acréscimos. Dói.",
        "Não desistiu nunca! O {team} buscou o empate quando ninguém mais acreditava.",
        "Balde de água fria no {other}. O {team} empatou no apagar das luzes.",
        "O relógio corria contra, e o {team} correu mais que ele. Empate heroico.",
        "O {other} vai lamentar: tinha a vitória na mão e deixou escapar no fim.",
    ),
    # 0 × 0 (~3% das análises; o placar sai em ~7% dos jogos parelhos).
    "zero_a_zero": (
        "Jogo amarrado, poucas chances e nenhum gol. Para quem gosta de tática, um prato cheio; "
        "para o resto, sonífero.",
        "0 × 0 justo. As defesas venceram o duelo e os atacantes ficaram devendo.",
        "Muito estudo, pouca ousadia. Ninguém quis arriscar e o placar não saiu do zero.",
        "Zero a zero e bocejos na arquibancada. Os goleiros quase não sujaram o uniforme.",
        "Duas muralhas em campo. Se o objetivo era não perder, os dois conseguiram.",
        "Faltou capricho na frente. Jogo travado, sem gols e sem graça.",
        "Os técnicos vão gostar do 0 × 0; a torcida, nem tanto.",
        "Jogo de paciência que terminou como começou: sem ninguém balançar a rede.",
    ),
    # Gol da vitória (por 1) aos 88' ou depois (~3%).
    "gol_no_fim": (
        "Decidido no apagar das luzes! O {winner} acreditou até o fim e foi recompensado.",
        "Gol no finzinho, festa do {winner} e silêncio do {loser}. Cruel, mas é o jogo.",
        "Quem sai antes do apito final perde o melhor. O {winner} venceu no último suspiro.",
        "O {loser} já se contentava com o empate. O {winner} não se contentou.",
        "Agonia até o fim e explosão no final! O {winner} arrancou a vitória no relógio.",
        "Gol no fim vale o mesmo, mas se comemora em dobro. Vitória do {winner}.",
    ),
    # Alguém fez 3 gols (~2,5%).
    "hattrick": (
        "Hat-trick de {player}! Quando ele está nesse dia, não tem marcação que resolva.",
        "{player} pediu a bola e resolveu: três gols e a partida no bolso.",
        "Três gols de {player}. O {loser} tentou de tudo, mas não teve como parar o homem.",
        "Pede música, {player}! Três gols e a bola do jogo é dele.",
        "{player} estava com o pé calibrado: três finalizações certeiras.",
        "Noite de artilheiro. {player} fez três e virou o assunto da rodada.",
    ),
    # O perdedor chutou 4+ vezes mais (~2%).
    "injustica": (
        "O {loser} jogou melhor, criou mais e perdeu. O futebol nem sempre é justo.",
        "Placar mentiroso: o {loser} mandou no jogo, mas quem fez os gols foi o {winner}.",
        "Eficiência venceu volume. O {winner} chegou pouco e matou; o {loser} perdoou demais.",
        "Quem não faz, toma. O {loser} vai aprender essa lição do jeito mais doloroso.",
        "O {winner} teve menos a bola e mais o placar. Às vezes basta.",
        "Futebol não premia o melhor, premia quem faz gol. O {winner} fez.",
    ),
    # Virou depois de estar 2+ gols atrás (~1%).
    "virada_insana": (
        "Que jogo, minha gente! O {winner} estava morto, enterrado, e ressuscitou para virar. "
        "Vai entrar para a história.",
        "Ninguém acreditava, nem a torcida do {winner}. Virada de quem não desiste nunca.",
        "O {loser} já fazia a festa e deixou escapar o impossível. Virada épica do {winner}!",
        "Isso não é virada, é milagre! O {winner} buscou o que parecia perdido.",
        "Guardem esse jogo: o {winner} perdia por dois e virou. Inacreditável!",
    ),
    # O perdedor teve expulsão e o vencedor não (~1%).
    "expulsao": (
        "A expulsão mudou tudo: com um a menos, o {loser} não aguentou.",
        "O vermelho custou caro. O {loser} pagou pela falta de cabeça.",
        "Com um a menos, o {loser} ficou sem pernas, e o {winner} aproveitou.",
        "Jogo decidido por um cartão. O {loser} se complicou sozinho.",
    ),
    # 6+ gols no jogo (~0,6%).
    "jogo_louco": (
        "Que jogo maluco! {score}, defesas abertas e diversão garantida para quem assistiu.",
        "Os goleiros não vão querer rever esse jogo. Chuva de gols: {score}.",
        "Defesa para quê? {score} e o torcedor saiu rouco.",
        "Jogo de pelada no melhor sentido: {score} e ninguém piscou.",
    ),
    # Alguém fez 4+ gols (~0,4%).
    "poker": (
        "{player} fez {goals} gols e levou a bola para casa. Atuação de gala, de outro planeta.",
        "Noite inesquecível de {player}: {goals} gols. O {loser} vai ter pesadelos com ele.",
        "{goals} gols de {player}! Isso não é atuação, é recital.",
        "Alguém segura {player}? {goals} gols numa só partida. Histórico.",
    ),
}

# Craques marcantes: quando um deles faz gol, o comentarista lembra a marca registrada dele
# (comemoração, apelido, jeito de jogar); não são citações inventadas. Cada entrada:
# palavras do nome (todas precisam estar no nome do jogador), se o nome tem de ser exato
# (Pelé não é o Abédi Pelé; Ronaldo Fenômeno não é o Cristiano Ronaldo) e as frases. Quem
# tem um craque desses faz gol quase todo jogo: são 8 frases por craque, e a escolha evita
# as 6 usadas por último (`src.commentary.pick`).
VINI = (
    "Baila, Vini! Vini Jr. marcou e dançou na beira do campo.",
    "Vini partiu para cima, deixou o marcador no chão e marcou.",
    "Vini Jr. desequilibrou de novo. Dança garantida na comemoração.",
    "Arrancada pela esquerda, drible curto e chute cruzado. Gol do Vini!",
    "Vini Jr. recebeu, encarou e venceu. O lateral ainda está procurando a bola.",
    "Velocidade e ousadia: Vini Jr. resolveu mais uma vez.",
    "O menino de São Gonçalo fez de novo. Gol de Vini Jr.!",
    "Quando a bola chega no Vini, a arquibancada já levanta.",
)
STARS: tuple[tuple[tuple[str, ...], bool, tuple[str, ...]], ...] = (
    (
        ("cristiano", "ronaldo"),
        False,
        (
            "SIUUUU! Cristiano Ronaldo marcou e fez a comemoração mais famosa do planeta.",
            "Cristiano Ronaldo subiu, marcou e girou no ar: SIUUU!",
            "CR7 não perdoa. Gol, salto e a torcida inteira gritando SIUUU.",
            "Cristiano Ronaldo apareceu na hora certa, como sempre. Máquina de fazer gols.",
            "Cabeçada no terceiro andar. Cristiano Ronaldo subiu mais alto que todo mundo.",
            "Pernas abertas na cobrança, respiração fundo... e gol. Cristiano é Cristiano.",
            "O homem dos recordes ampliou a conta. Mais um de Cristiano Ronaldo!",
            "Cristiano Ronaldo pediu calma com as mãos depois do gol. Ele resolve.",
        ),
    ),
    (
        ("messi",),
        False,
        (
            "Messi sendo Messi. Tem coisa que não se explica, só se assiste.",
            "Gol de Messi, dedos para o céu. O GOAT não tira folga.",
            "A bola parece colada no pé dele. Messi marcou e fez parecer fácil.",
            "Messi caminhou o jogo todo e decidiu em um lance. Gênio é assim.",
            "Arrancada pela direita, corte para a canhota e bola no ângulo. Assinatura de Messi.",
            "Messi deixou três marcadores para trás como se fossem cones.",
            "La Pulga fez mais uma. Até a torcida adversária aplaude.",
            "Marcação dupla, tripla, não adianta: Messi encontra o caminho.",
        ),
    ),
    (
        ("neymar",),
        False,
        (
            "Ousadia e alegria! Neymar deixou a sua marca.",
            "Neymar marcou e fez questão de sorrir para a câmera. Moleque Ney!",
            "Drible, finta e gol. Neymar jogou como se estivesse no quintal de casa.",
            "Caneta, chapéu e bola na rede. Neymar deu show.",
            "Neymar parou, olhou o goleiro e tocou no cantinho. Frieza total.",
            "O menino da Vila cresceu e continua fazendo arte. Gol de Neymar!",
            "Neymar dançou na comemoração, e a torcida foi junto.",
            "Quando Neymar está inspirado, não tem lateral que aguente.",
        ),
    ),
    (
        ("mbappe",),
        False,
        (
            "Mbappé ligou o turbo, marcou e cruzou os braços na comemoração.",
            "Ninguém alcança Mbappé em velocidade. Arrancou e guardou.",
            "Mbappé apareceu em velocidade de foguete e a bola foi parar na rede.",
            "A defesa piscou e Mbappé já estava na cara do gol. Gol!",
            "Mbappé finalizou com a frieza de quem já ganhou Copa do Mundo.",
            "Contra-ataque com Mbappé é meio gol. Hoje foi o gol inteiro.",
            "Mbappé recebeu na esquerda, cortou para dentro e bateu no canto. Clássico.",
            "Velocidade, técnica e frieza. Mbappé tem tudo, e mostrou.",
        ),
    ),
    (
        ("haaland",),
        False,
        (
            "Haaland marcou e sentou para meditar. O robô não erra.",
            "Bola na área, Haaland na rede. Simples assim.",
            "O ciborgue norueguês fez mais um. Haaland é gol.",
            "Haaland pegou a sobra e fuzilou. A rede quase rasgou.",
            "Força bruta e faro de gol: mais um do Haaland.",
            "Quase dois metros de centroavante e um pé calibrado. Haaland marcou.",
            "Haaland toca pouco na bola, mas quando toca... é gol.",
            "A defesa sabia que a bola ia para o Haaland. Não adiantou.",
        ),
    ),
    (("vinicius", "junior"), False, VINI),
    (("vini", "jr"), False, VINI),
    (
        ("salah",),
        False,
        (
            "Salah marcou e se ajoelhou em agradecimento. O Rei do Egito.",
            "Corte para a perna esquerda e bola no canto: assinatura de Salah.",
            "O Faraó fez mais um. Salah é decisivo.",
            "Salah apareceu pela direita e finalizou de canhota. Indefensável.",
            "Mo Salah fez a festa da torcida mais uma vez.",
            "Rápido, frio e certeiro: gol de Salah.",
            "Salah encontrou o espaço que não existia e marcou.",
            "Mais um gol do egípcio. Salah não para.",
        ),
    ),
    (
        ("ibrahimovic",),
        False,
        (
            "Zlatan não pede licença: marca e lembra a todos quem é o Zlatan.",
            "Ibrahimović marcou com a marra de sempre. Ninguém comemora como ele.",
            "Gol acrobático? Com Ibrahimović, nada é impossível.",
            "Voleio de taekwondo e bola na rede. Zlatan sendo Zlatan.",
            "Ibrahimović dominou no peito, girou e bateu. Facilidade absurda.",
            "Zlatan marcou e abriu os braços. Ele sabe que é o centro das atenções.",
            "Quase dois metros de talento e marra: Ibrahimović decidiu.",
            "Ibra fez mais um e não demonstrou nenhuma surpresa.",
        ),
    ),
    (
        ("ronaldinho",),
        False,
        (
            "Ronaldinho marcou com aquele sorriso no rosto. Joga bonito!",
            "Elástico, chapéu e gol. Ronaldinho Gaúcho fazendo arte.",
            "Ronaldinho olhou para um lado e tocou para o outro. Magia pura.",
            "Gol de Ronaldinho, e o sorriso mais famoso do futebol.",
            "Ronaldinho fez a bola dançar antes de mandar para a rede.",
            "Falta de Ronaldinho é perigo: a bola fez a curva e entrou.",
            "Bruxaria! Ronaldinho Gaúcho inventou um gol do nada.",
            "Ronaldinho comemorou com o hang loose, e a torcida foi ao delírio.",
        ),
    ),
    (
        ("pele",),
        True,
        (
            "O Rei marcou! Soco no ar, como só Pelé sabia comemorar.",
            "Pelé fez mais um. Mil e tantos gols e ainda com fome.",
            "Majestade em campo. Gol do Rei Pelé!",
            "Chapéu no zagueiro e voleio. Coisa de Rei.",
            "Pelé subiu como se tivesse mola. Cabeçada certeira.",
            "O Atleta do Século mostrou por que tem esse título.",
            "Gol de Pelé! O resto do mundo só pode assistir.",
            "Três vezes campeão do mundo e com o mesmo apetite. Pelé marcou.",
        ),
    ),
    (
        ("romario",),
        True,
        (
            "Romário na área é gol. O Baixinho não perdoa.",
            "Um toque, uma finta, um gol. Romário, o rei da pequena área.",
            "O Baixinho resolveu com a calma de sempre. Gol de Romário!",
            "Romário recebeu de costas, girou e tocou na saída do goleiro.",
            "Romário não corre muito; não precisa. Bastou uma bola.",
            "Romário dentro da área: sentença de gol.",
            "O Baixinho deixou o zagueiro na saudade e marcou.",
            "Gol de Romário! Faro de artilheiro não se ensina.",
        ),
    ),
    (
        ("ronaldo", "fenomeno"),
        True,
        (
            "O Fenômeno arrancou e ninguém segurou. Gol de Ronaldo!",
            "Pedalada, arrancada e gol. Ronaldo Fenômeno em estado puro.",
            "Ronaldo Fenômeno deixou o goleiro sentado e tocou para a rede.",
            "O Fenômeno apareceu entre os zagueiros e finalizou de primeira.",
            "Ronaldo comemorou balançando o dedo. Clássico!",
            "Velocidade e potência: Ronaldo Fenômeno atropelou a defesa.",
            "Ronaldo driblou o goleiro e entrou com bola e tudo.",
            "Fenômeno é pouco. Ronaldo fez mais um.",
        ),
    ),
    (
        ("maradona",),
        False,
        (
            "Barrilete cósmico! De que planeta viniste? Gol de Maradona!",
            "Diego pegou a bola e ninguém mais tocou nela até a rede. El Pibe de Oro!",
            "Maradona driblou meio time e marcou. Gênio do futebol.",
            "Canhota mágica: Maradona colocou a bola onde quis.",
            "Dieeego! Maradona fez a torcida cantar o nome dele.",
            "Maradona saiu driblando do meio de campo. A defesa ainda está tonta.",
            "Gol de Maradona com a mão? Não, dessa vez foi com o pé mesmo.",
            "El Diez fez mais um. Arte em forma de gol.",
        ),
    ),
    (
        ("garrincha",),
        False,
        (
            "Garrincha deixou mais um João no chão. Alegria do povo!",
            "Drible para a direita, sempre para a direita, e ninguém segura. Gol de Garrincha!",
            "Mané Garrincha fez a festa da arquibancada mais uma vez.",
            "Todo mundo sabe para onde Garrincha vai. Ninguém consegue impedir.",
            "Garrincha driblou o lateral duas vezes, só por diversão, e marcou.",
            "O Anjo de Pernas Tortas voou mais uma vez.",
            "Garrincha entortou a defesa e mandou para a rede. Que alegria!",
            "Mané fez mais um, e a torcida riu junto com ele.",
        ),
    ),
    (
        ("zidane",),
        False,
        (
            "Zidane marcou com a elegância de sempre. Classe pura.",
            "Roleta, toque de calcanhar e gol. Zizou é arte.",
            "Zidane jogou de terno e gravata e ainda marcou.",
            "Voleio de canhota, no ângulo. Zidane fez parecer simples.",
            "Zizou dominou no peito e bateu sem deixar cair. Que categoria.",
            "Zidane parecia jogar em câmera lenta, mas sempre um passo à frente. Gol!",
            "Zidane decidiu com a frieza de quem já ganhou tudo.",
            "Gol de Zidane, aplaudido até pelos adversários.",
        ),
    ),
    (
        ("zico",),
        True,
        (
            "Galinho de Quintino! Zico marcou e a torcida foi à loucura.",
            "Falta perto da área? É do Zico. E foi gol.",
            "Zico fez mais um. O Galinho é sinônimo de gol.",
            "Zico colocou a bola por cima da barreira, no ângulo. Perfeito.",
            "Passe, tabela e gol: Zico jogou e fez jogar.",
            "O camisa 10 apareceu na hora certa. Gol de Zico!",
            "Zico bateu colocado, sem chance para o goleiro.",
            "Gol do Galinho! Zico decidiu com classe.",
        ),
    ),
    (
        ("bellingham",),
        False,
        (
            "Bellingham marcou e abriu os braços no meio da festa. Hey Jude!",
            "Bellingham chegou de trás e finalizou como atacante. Que jogador!",
            "Jude Bellingham decidiu de novo. Braços abertos e torcida em êxtase.",
            "Meia que faz gol de centroavante: Bellingham apareceu na área.",
            "Bellingham dominou, protegeu e bateu. Maturidade de veterano.",
            "Bellingham comemorou de braços abertos, e a torcida cantou junto.",
            "Chegada surpresa de Bellingham e bola na rede.",
            "Jovem e decisivo: mais um de Jude Bellingham.",
        ),
    ),
    (
        ("lewandowski",),
        False,
        (
            "Lewandowski na área é gol certo. Máquina polonesa.",
            "Um toque de Lewandowski e a bola na rede. Matador.",
            "Lewandowski não precisa de muito: uma chance, um gol.",
            "Posicionamento perfeito, finalização perfeita. Lewandowski.",
            "Lewandowski girou em cima do zagueiro e bateu no canto.",
            "Centroavante de manual: Lewandowski estava no lugar certo.",
            "Mais um para a conta de Lewandowski. O polonês não cansa.",
            "Lewandowski cabeceou firme, sem chance para o goleiro.",
        ),
    ),
)

# Rivalidades (amistosos e campeonato entre as mesmas duas pessoas). Marcadores: {winner} e
# {loser} deste jogo, {home} e {away}, {n} (sequência, jejum ou número do confronto) e
# {record} ("Ana FC 5 × 3 Beto FC").
RIVALRY: dict[str, tuple[str, ...]] = {
    # Primeiro jogo entre os dois, com vencedor.
    "primeiro": (
        "Primeiro capítulo de uma nova rivalidade: o {winner} sai na frente contra o {loser}.",
        "Começa a história de {home} × {away}, e o primeiro ponto é do {winner}.",
        "O {winner} venceu o primeiro duelo. O {loser} já tem uma revanche para buscar.",
    ),
    # Primeiro jogo entre os dois, sem vencedor (só no ida e volta).
    "primeiro_empate": (
        "Primeiro encontro sem vencedor. A rivalidade {home} × {away} começa equilibrada.",
        "Ninguém levou a melhor no primeiro duelo. Que venha o próximo.",
    ),
    # O vencedor ganhou os últimos {n} (3 ou mais).
    "freguesia": (
        "{n}ª vitória seguida do {winner} sobre o {loser}. Freguesia confirmada!",
        "O {loser} virou freguês: {n} derrotas seguidas para o {winner}.",
        "De novo o {winner}! São {n} vitórias seguidas no confronto. O {loser} precisa de um "
        "exorcista.",
        "O {winner} tem o número do {loser}: {n} jogos, {n} vitórias seguidas.",
    ),
    # O vencedor não ganhava havia {n} jogos (3 ou mais).
    "jejum": (
        "Acabou o jejum! O {winner} não vencia o {loser} havia {n} jogos.",
        "Fim da freguesia! Depois de {n} jogos sem vencer, o {winner} enfim derrota o {loser}.",
        "O {winner} quebrou a escrita: {n} jogos depois, voltou a vencer o {loser}.",
    ),
    # Confronto de número redondo (10º, 25º, 50º, 100º).
    "marco": (
        "{n}º confronto entre {home} e {away}! Retrospecto agora: {record}.",
        "Já são {n} jogos dessa rivalidade. Placar geral: {record}.",
    ),
    # Com esta vitória, o vencedor passou à frente no retrospecto.
    "virou": (
        "Com essa, o {winner} passa à frente no retrospecto: {record}.",
        "Virada no confronto direto! Agora é {record}.",
        "O {winner} assume a liderança da rivalidade: {record}.",
    ),
    # Com esta vitória, o retrospecto ficou empatado.
    "igualou": (
        "O {winner} empata o retrospecto: {record}. Rivalidade pegando fogo!",
        "Tudo igual de novo no confronto direto: {record}.",
        "O {loser} estava na frente na rivalidade, mas o {winner} igualou: {record}.",
    ),
}
