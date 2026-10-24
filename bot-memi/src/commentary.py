"""A análise do comentarista no fim do jogo: uma frase sobre o que a partida foi.

A partida cai na primeira situação que bate (da mais marcante para a mais comum): virada
insana antes de virada, hat-trick antes de vitória fácil, e assim por diante. Cada situação
tem várias frases; a escolha é sempre a mesma para a mesma partida, então a mensagem não
muda a cada edição.
"""

from __future__ import annotations

import random
from collections import Counter
from collections.abc import Sequence
from typing import Any

from src.db import normalize_player_name

# Marcadores: {winner}, {loser}, {home}, {away}, {player}, {goals}, {keeper}, {score}.
PHRASES: dict[str, tuple[str, ...]] = {
    "penaltis": (
        "Noventa minutos não bastaram. Nos pênaltis, o {winner} teve mais frieza e o {loser} "
        "vai dormir pensando na cobrança perdida.",
        "Decisão na marca da cal é loteria? O {winner} vai dizer que foi treino. Classificação "
        "suada.",
        "Coração na boca até a última cobrança. O {winner} sobreviveu; o {loser} saiu de cabeça "
        "erguida.",
    ),
    "virada_insana": (
        "Que jogo, minha gente! O {winner} estava morto, enterrado, e ressuscitou para virar. "
        "Vai entrar para a história.",
        "Ninguém acreditava, nem a torcida do {winner}. Virada de quem não desiste nunca.",
        "O {loser} já fazia a festa e deixou escapar o impossível. Virada épica do {winner}!",
    ),
    "virada": (
        "Começou mal, terminou sorrindo. O {winner} buscou a virada com personalidade.",
        "O {loser} saiu na frente, mas não soube segurar. Virada merecida do {winner}.",
        "Time grande se mede na adversidade: o {winner} tomou o gol e foi buscar o resultado.",
    ),
    "poker": (
        "{player} fez {goals} gols e levou a bola para casa. Atuação de gala, de outro planeta.",
        "Noite inesquecível de {player}: {goals} gols. O {loser} vai ter pesadelos com ele.",
    ),
    "hattrick": (
        "Hat-trick de {player}! Quando ele está nesse dia, não tem marcação que resolva.",
        "{player} pediu a bola e resolveu: três gols e a partida no bolso.",
        "Três gols de {player}. O {loser} tentou de tudo, mas não teve como parar o homem.",
    ),
    "goleada": (
        "Atropelo! O {winner} passou por cima do {loser} sem pedir licença: {score}.",
        "Foi treino de luxo para o {winner}. O {loser} precisa conversar muito no vestiário.",
        "Massacre. O {winner} fez o que quis do primeiro ao último minuto.",
    ),
    "empate_fim": (
        "Quando parecia acabado, o {team} arrancou o empate no fim. Futebol é isso!",
        "O {other} já contava os três pontos e viu o empate escapar nos acréscimos. Dói.",
    ),
    "gol_no_fim": (
        "Decidido no apagar das luzes! O {winner} acreditou até o fim e foi recompensado.",
        "Gol no finzinho, festa do {winner} e silêncio do {loser}. Cruel, mas é o jogo.",
    ),
    "injustica": (
        "O {loser} jogou melhor, criou mais e perdeu. O futebol nem sempre é justo.",
        "Placar mentiroso: o {loser} mandou no jogo, mas quem fez os gols foi o {winner}.",
        "Eficiência venceu volume. O {winner} chegou pouco e matou; o {loser} perdoou demais.",
    ),
    "goleiro": (
        "{keeper} fechou o gol! Sem ele, o resultado seria outro.",
        "Noite de paredão: {keeper} pegou tudo o que chegou.",
    ),
    "dominio": (
        "Domínio do começo ao fim. O {winner} teve a bola, as chances e o resultado.",
        "O {winner} jogou no campo do {loser} a partida inteira. Vitória incontestável.",
    ),
    "vitoria_facil": (
        "Vitória tranquila do {winner}, sem sustos. Fez o dever de casa com sobras.",
        "O {winner} controlou o jogo e venceu sem precisar acelerar muito.",
        "Resultado seguro do {winner}. O {loser} até tentou, mas faltou poder de fogo.",
    ),
    "expulsao": (
        "A expulsão mudou tudo: com um a menos, o {loser} não aguentou.",
        "O vermelho custou caro. O {loser} pagou pela falta de cabeça.",
    ),
    "jogo_louco": (
        "Que jogo maluco! {score}, defesas abertas e diversão garantida para quem assistiu.",
        "Os goleiros não vão querer rever esse jogo. Chuva de gols: {score}.",
    ),
    "zero_a_zero": (
        "Jogo amarrado, poucas chances e nenhum gol. Para quem gosta de tática, um prato cheio; "
        "para o resto, sonífero.",
        "0 × 0 justo. As defesas venceram o duelo e os atacantes ficaram devendo.",
        "Muito estudo, pouca ousadia. Ninguém quis arriscar e o placar não saiu do zero.",
    ),
    "empate": (
        "Empate que fica de bom tamanho. Os dois tiveram seus momentos e dividiram os pontos.",
        "Ninguém mereceu perder. Jogo equilibrado, placar justo.",
        "Ficou no empate, mas teve emoção. {home} e {away} saem com um pontinho cada.",
    ),
    "jogo_dificil": (
        "Vitória suada do {winner}! Jogo pegado, decidido nos detalhes.",
        "Não foi bonito, mas foi eficiente. O {winner} sofreu e segurou o resultado.",
        "Três pontos com muito suor. O {loser} vendeu caro a derrota.",
    ),
}


def _final_score(regular: Sequence[dict[str, Any]]) -> tuple[int, int]:
    last = regular[-1] if regular else {"home_goals": 0, "away_goals": 0}
    return int(last.get("home_goals", 0)), int(last.get("away_goals", 0))


def _situation(events: Sequence[dict[str, Any]]) -> tuple[str, dict[str, str]] | None:
    """A situação da partida e os nomes para as frases ("winner", "player"…)."""
    regular = [event for event in events if event["kind"] not in {"penalties", "penalty"}]
    if not regular:
        return None
    shootout = [event for event in events if event["kind"] == "penalty"]
    home, away = _final_score(regular)
    goals = [event for event in regular if event["kind"] == "goal"]
    stats = next((event.get("stats") for event in regular if event["kind"] == "fulltime"), None)
    winner = "home" if home > away else "away" if away > home else None
    if winner is None and shootout:
        last = shootout[-1]
        winner = "home" if last["home_goals"] > last["away_goals"] else "away"
        return "penaltis", {"side": winner}
    loser = {"home": "away", "away": "home"}.get(winner or "")
    margin, total = abs(home - away), home + away
    info: dict[str, str] = {"side": winner or ""}

    if winner:
        # Maior desvantagem que o vencedor teve durante o jogo.
        worst = 0
        for event in goals:
            mine, theirs = (
                (event["home_goals"], event["away_goals"])
                if winner == "home"
                else (event["away_goals"], event["home_goals"])
            )
            worst = max(worst, theirs - mine)
        if worst >= 2:
            return "virada_insana", info
        if worst == 1:
            return "virada", info
    scorers = Counter(
        (event["side"], event["player"])
        for event in goals
        if event.get("play") != "contra" and event.get("player")
    )
    if scorers:
        (side, player), count = scorers.most_common(1)[0]
        if count >= 3:
            data = {"side": side, "player": player, "goals": str(count)}
            return ("poker" if count >= 4 else "hattrick"), data
    if winner and margin >= 4:
        return "goleada", info
    if winner is None and any(
        e["kind"] == "feat" and e.get("play") == "empate_fim" for e in regular
    ):
        tied = next(e for e in regular if e["kind"] == "feat" and e.get("play") == "empate_fim")
        return "empate_fim", {"side": tied["side"]}
    if (
        winner
        and margin == 1
        and goals
        and goals[-1]["side"] == winner
        and goals[-1]["minute"] >= 88
    ):
        return "gol_no_fim", info
    shots = (stats or {}).get("shots")
    if winner and shots:
        index = {"home": 0, "away": 1}
        if shots[index[loser]] >= shots[index[winner]] + 4:
            return "injustica", info
    if total <= 1 and stats and stats.get("best", {}).get("rating", 0) >= 8:
        saves = Counter(
            (event["detail"], event["side"])
            for event in regular
            if event["kind"] in {"save", "miracle"} and event.get("detail")
        )
        if saves:
            (keeper, shooter_side), count = saves.most_common(1)[0]
            if count >= 3:
                side = "away" if shooter_side == "home" else "home"
                return "goleiro", {"side": side, "keeper": keeper}
    if winner and stats:
        index = {"home": 0, "away": 1}
        possession = stats.get("possession") or (50, 50)
        if possession[index[winner]] >= 60 and shots and margin >= 2:
            return "dominio", info
    if winner and margin >= 2:
        return "vitoria_facil", info
    reds = Counter(event.get("side") for event in regular if event["kind"] == "red")
    if winner and reds[loser] and not reds[winner]:
        return "expulsao", info
    if total >= 6:
        return "jogo_louco", info
    if total == 0:
        return "zero_a_zero", info
    if winner is None:
        return "empate", info
    return "jogo_dificil", info


def match_commentary(
    events: Sequence[dict[str, Any]], home_name: str, away_name: str
) -> str | None:
    """A frase do comentarista para a partida encerrada (None se não há lances)."""
    found = _situation(events)
    if found is None:
        return None
    situation, info = found
    names = {"home": home_name, "away": away_name}
    side = info.get("side") or "home"
    other = "away" if side == "home" else "home"
    regular = [event for event in events if event["kind"] not in {"penalties", "penalty"}]
    home, away = _final_score(regular)
    values = {
        "winner": names[side],
        "loser": names[other],
        "team": names[side],
        "other": names[other],
        "home": home_name,
        "away": away_name,
        "player": info.get("player", ""),
        "goals": info.get("goals", ""),
        "keeper": info.get("keeper", ""),
        "score": f"{home} × {away}",
    }
    # Mesma partida, mesma frase (o embed final é editado mais de uma vez).
    seed = f"{home_name}|{away_name}|{len(events)}|{home}x{away}|{situation}"
    phrase = random.Random(seed).choice(PHRASES[situation])
    return phrase.format(**values)


# Craques marcantes: quando um deles faz gol, o comentarista lembra a marca registrada dele
# (comemoração, apelido, narração famosa). Cada entrada: palavras do nome (todas precisam
# estar no nome do jogador) e as frases. `exact` exige o nome inteiro (Pelé não é o Abédi
# Pelé; Ronaldo Fenômeno não é o Cristiano Ronaldo).
STARS: tuple[tuple[tuple[str, ...], bool, tuple[str, ...]], ...] = (
    (
        ("cristiano", "ronaldo"),
        False,
        (
            "SIUUUU! Cristiano Ronaldo marcou e fez a comemoração mais famosa do planeta.",
            "Cristiano Ronaldo subiu, marcou e girou no ar: SIUUU!",
        ),
    ),
    (
        ("messi",),
        False,
        (
            "Messi sendo Messi. Tem coisa que não se explica, só se assiste.",
            "Gol de Messi, dedos para o céu. O GOAT não tira folga.",
        ),
    ),
    (
        ("neymar",),
        False,
        (
            "Ousadia e alegria! Neymar deixou a sua marca.",
            "Neymar marcou e fez questão de sorrir para a câmera. Moleque Ney!",
        ),
    ),
    (("mbappe",), False, ("Mbappé ligou o turbo, marcou e cruzou os braços na comemoração.",)),
    (
        ("haaland",),
        False,
        (
            "Haaland marcou e sentou para meditar. O robô não erra.",
            "Bola na área, Haaland na rede. Simples assim.",
        ),
    ),
    (
        ("vinicius", "junior"),
        False,
        ("Baila, Vini! Vinícius Júnior marcou e dançou na beira do campo.",),
    ),
    (("vini", "jr"), False, ("Baila, Vini! Vini Jr. marcou e dançou na beira do campo.",)),
    (("salah",), False, ("Salah marcou e se ajoelhou em agradecimento. O Rei do Egito.",)),
    (
        ("ibrahimovic",),
        False,
        ("Zlatan não pede licença: marca e lembra a todos quem é o Zlatan.",),
    ),
    (("ronaldinho",), False, ("Ronaldinho marcou com aquele sorriso no rosto. Joga bonito!",)),
    (("pele",), True, ("O Rei marcou! Soco no ar, como só Pelé sabia comemorar.",)),
    (("romario",), True, ("Romário na área é gol. O Baixinho não perdoa.",)),
    (("ronaldo", "fenomeno"), True, ("O Fenômeno arrancou e ninguém segurou. Gol de Ronaldo!",)),
    (("maradona",), False, ("Barrilete cósmico! De que planeta viniste? Gol de Maradona!",)),
    (("garrincha",), False, ("Garrincha deixou mais um João no chão. Alegria do povo!",)),
    (("zidane",), False, ("Zidane marcou com a elegância de sempre. Classe pura.",)),
    (("zico",), True, ("Galinho de Quintino! Zico marcou e a torcida foi à loucura.",)),
    (("bellingham",), False, ("Bellingham marcou e abriu os braços no meio da festa. Hey Jude!",)),
    (("lewandowski",), False, ("Lewandowski na área é gol certo. Máquina polonesa.",)),
)


def _star(name: str) -> tuple[str, ...] | None:
    words = normalize_player_name(name).split()
    for keys, exact, phrases in STARS:
        if (words == list(keys)) if exact else set(keys) <= set(words):
            return phrases
    return None


def star_lines(events: Sequence[dict[str, Any]], limit: int = 2) -> list[str]:
    """Frases dos craques que fizeram gol na partida (no máximo `limit`)."""
    lines: list[str] = []
    seen: set[str] = set()
    for event in events:
        name = event.get("player") or ""
        if event["kind"] != "goal" or event.get("play") == "contra" or name in seen:
            continue
        seen.add(name)
        phrases = _star(name)
        if phrases:
            lines.append(random.Random(f"{name}|{len(events)}").choice(phrases))
        if len(lines) >= limit:
            break
    return lines
