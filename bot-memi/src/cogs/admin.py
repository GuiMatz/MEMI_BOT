"""Comandos de admin (`src.admins`): testes, correções e ajustes de conta.

`mm!admin` lista tudo. Os ajustes (Memilets, itens, jogadores, rolls) mexem direto no banco,
sem as regras do jogo, para compensar alguém ou corrigir um erro.
"""

from __future__ import annotations

import asyncio
import random
import subprocess
import time
from io import BytesIO
from pathlib import Path

import discord
from discord.ext import commands

from src import admin_tools
from src.admin_tools import AdminError
from src.admins import is_admin
from src.db import DATABASE_PATH
from src.inventory import INVENTORY_ITEMS
from src.dedupe import find_duplicates, remove_duplicates
from src.narration import narrate
from src.simulation import penalty_events, penalty_shootout_kicks, simulate_match

TEST_RATINGS = [80, 75, 90, 77, 60, 50]
STARTED = time.monotonic()
REPO = Path(__file__).resolve().parents[3]


def _version() -> str:
    """O commit que está rodando (para conferir se a atualização entrou)."""
    try:
        return subprocess.run(
            ["git", "log", "-1", "--format=%h · %s"],
            cwd=REPO,
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        ).stdout.strip()[:90]
    except (OSError, subprocess.SubprocessError):
        return "desconhecida"


def _duration(seconds: float) -> str:
    minutes, _ = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)
    return (f"{days}d " if days else "") + f"{hours}h {minutes:02d}min"


def _size(value: int) -> str:
    return f"{value / 1024 / 1024:.1f} MB"


# Nomes de mentira para os jogos de teste (cada time de teste pega 11 diferentes).
TEST_FIRST = (
    "Rafael",
    "Bruno",
    "Diego",
    "Thiago",
    "Lucas",
    "Matheus",
    "Caio",
    "Igor",
    "Vitor",
    "Danilo",
    "Renan",
    "Felipe",
    "Gustavo",
    "André",
    "Hugo",
    "Otávio",
)
TEST_LAST = (
    "Moura",
    "Teixeira",
    "Ramos",
    "Pires",
    "Farias",
    "Nogueira",
    "Castro",
    "Lopes",
    "Barros",
    "Cardoso",
    "Rezende",
    "Matos",
    "Freitas",
    "Aguiar",
    "Siqueira",
    "Vidal",
)
TEST_COLORS = ("#C8102E", "#1E5AA8", "#1F7A3A", "#E8B84A", "#7B3FB0", "#333333")


def _test_lineup(overall: int, prefix: str) -> list[dict]:
    positions = ["GOL", "ZAG", "ZAG", "LE", "LD", "MC", "MC", "MEI", "PE", "PD", "CA"]
    names = random.Random(prefix).sample(
        [f"{first} {last}" for first in TEST_FIRST for last in TEST_LAST], len(positions)
    )
    return [
        {
            "id": index + 1,
            "name": names[index],
            "display_name": names[index],
            "position": position,
            "overall": overall,
        }
        for index, position in enumerate(positions)
    ]


def _test_color(name: str) -> str:
    return TEST_COLORS[sum(map(ord, name)) % len(TEST_COLORS)]


async def _play_final(send, home: tuple[str, int], away: tuple[str, int], rng, title: str):
    """Joga uma partida narrada; empate vai para os pênaltis."""
    home_players = _test_lineup(home[1], home[0])
    away_players = _test_lineup(away[1], away[0])
    result = simulate_match(home[0], home_players, away[0], away_players, rng=rng)
    timeline = list(result.events)
    penalties = None
    if result.home_goals == result.away_goals:
        kicks = penalty_shootout_kicks(home_players, away_players, rng=rng)
        timeline += penalty_events(kicks, home[0], away[0])
        penalties = (kicks[-1].home_score, kicks[-1].away_score)
    home_color, away_color = _test_color(home[0]), _test_color(away[0])
    if home_color == away_color:
        away_color = TEST_COLORS[(TEST_COLORS.index(home_color) + 1) % len(TEST_COLORS)]
    await narrate(
        send,
        home[0],
        away[0],
        timeline,
        title=title,
        penalties=penalties,
        home_color=home_color,
        away_color=away_color,
    )
    if penalties:
        return home[0] if penalties[0] > penalties[1] else away[0]
    return home[0] if result.home_goals > result.away_goals else away[0]


# Painel do `mm!admin` -------------------------------------------------------------------------
# Cada página: (rótulo do botão, emoji, cor, descrição, [(como usar, o que faz)]).
ADMIN_PAGES: dict[str, tuple[str, str, int, str, list[tuple[str, str]]]] = {
    "inicio": (
        "Visão geral",
        "🛠️",
        0xC0392B,
        "Comandos só para admins do bot: o dono, lurram e quem estiver em `MEMI_ADMIN_IDS`. "
        "Os de campeonato também aceitam quem tem **Gerenciar servidor**.\n\n"
        "Escolha uma categoria nos botões.",
        [
            ("👤 Contas", "Memilets, itens, jogadores, rolls e claim"),
            ("🏆 Campeonato", "jogar, adiantar, pausar, horário, status, remover, cancelar"),
            ("🤖 Bot", "status, backup, reiniciar e leitura do histórico"),
            ("🧪 Testes", "partidas e campeonatos de mentira, imagens e duplicados"),
        ],
    ),
    "contas": (
        "Contas",
        "👤",
        0x2E86C1,
        "Mexem direto no banco, sem as regras do jogo: para compensar alguém, premiar um "
        "evento ou corrigir um erro.",
        [
            ("mm!admin dar @pessoa 5000", "Dá Memilets."),
            ("mm!admin tirar @pessoa 5000", "Tira Memilets (o saldo não fica negativo)."),
            (
                "mm!admin item @pessoa caixa_epica 2",
                "Dá itens: caixa, caixa_epica, pacote, pacote_epico. Número negativo tira.",
            ),
            (
                "mm!admin jogador @pessoa <nome ou ID> [forçar]",
                "Entrega um jogador livre. Com `forçar` no fim, tira de quem já tem (e do time "
                "dele). Se o nome servir para mais de um, mostra os IDs.",
            ),
            (
                "mm!admin resetar @pessoa rolls|claim|tudo",
                "Devolve os rolls do ciclo e/ou libera o claim na hora.",
            ),
        ],
    ),
    "campeonato": (
        "Campeonato",
        "🏆",
        0xE8B84A,
        "Pintos Corridos e Copa IOS do servidor.",
        [
            ("mm!campeonato status", "Tudo o que decide se os jogos saem, numa mensagem só."),
            ("mm!campeonato canal [#canal]", "Canal onde os jogos são narrados."),
            ("mm!campeonato ativar", "Liga os jogos diários no servidor."),
            ("mm!campeonato jogar", "Começa a edição (se estiver esperando) e joga o dia agora."),
            (
                "mm!campeonato adiantar",
                "Joga já a próxima rodada (ou fase da Copa) e adianta o calendário um dia.",
            ),
            (
                "mm!campeonato pausar · retomar",
                "Para e volta os jogos; ao retomar, o calendário anda os dias parados.",
            ),
            ("mm!campeonato horario 21:00", "Horário dos jogos (Brasília; padrão 19h)."),
            (
                "mm!campeonato remover @pessoa",
                "Tira um time da edição; os jogos que faltam dele viram W.O.",
            ),
            (
                "mm!campeonato cancelar confirmar",
                "Encerra a edição sem prêmios e abre a próxima.",
            ),
        ],
    ),
    "bot": (
        "Bot",
        "🤖",
        0x5865F2,
        "Manutenção do bot. Os da segunda parte são do bot principal (MeMi BOT).",
        [
            ("mm!admin status", "Versão (commit) rodando, tempo ligado, ping, banco e backup."),
            ("mm!admin backup", "Cópia do banco do jogo agora (guarda as 10 últimas)."),
            (
                "mm!reiniciar",
                "Fecha o bot com calma e abre de novo (também: reiniciar.bat ou o ícone).",
            ),
            ("mm!read [tudo | #canal]", "Lê o histórico dos canais (músicas e atividade)."),
            ("mm!scan", "Leitura completa de todos os canais."),
            ("mm!exportar", "Manda tudo o que o bot guardou em planilhas CSV."),
            ("mm!give @pessoa TAG[, TAG...]", "Dá títulos (tags) de perfil."),
            ("mm!mudaedump", "Exporta mensagens do Mudae em JSON (calibração)."),
        ],
    ),
    "testes": (
        "Testes",
        "🧪",
        0x8E44AD,
        "Não mexem nos times de verdade.",
        [
            ("mm!testeamistoso", "Partida de teste narrada (empate vai aos pênaltis)."),
            ("mm!testecampeonato <n>", "Campeonato de teste com n times e final narrada."),
            ("mm!testeimagem", "A escalação em embed, anexo e Components V2."),
            ("mm!admin duplicados", "Procura e remove jogadores duplicados (com confirmação)."),
        ],
    ),
}


def admin_page(key: str) -> discord.Embed:
    label, emoji, color, description, rows = ADMIN_PAGES[key]
    title = "🛠️ Admin do MEMI" if key == "inicio" else f"{emoji} Admin · {label}"
    embed = discord.Embed(title=title, description=description, color=color)
    for usage, detail in rows:
        name = usage if key == "inicio" else f"`{usage}`"
        embed.add_field(name=name, value=detail, inline=False)
    embed.set_footer(text="Só admins do bot usam esses comandos.")
    return embed


class AdminPanel(discord.ui.View):
    """Botões do `mm!admin`: cada um troca a página na mesma mensagem."""

    def __init__(self, owner, page: str = "inicio"):
        super().__init__(timeout=300)
        self.owner = owner
        self.message: discord.Message | None = None
        for key, (label, emoji, *_rest) in ADMIN_PAGES.items():
            button = discord.ui.Button(
                label=label,
                emoji=emoji,
                style=discord.ButtonStyle.primary if key == page else discord.ButtonStyle.secondary,
                custom_id=f"admin:{key}",
            )
            button.callback = self._switch(key)
            self.add_item(button)

    def _switch(self, page: str):
        async def callback(interaction: discord.Interaction) -> None:
            if interaction.user.id != self.owner.id:
                await interaction.response.send_message(
                    "Esse painel é de quem abriu.", ephemeral=True
                )
                return
            for item in self.children:
                if isinstance(item, discord.ui.Button):
                    active = item.custom_id == f"admin:{page}"
                    item.style = (
                        discord.ButtonStyle.primary if active else discord.ButtonStyle.secondary
                    )
            await interaction.response.edit_message(embed=admin_page(page), view=self)

        return callback

    async def on_timeout(self) -> None:
        if self.message is None:
            return
        for item in self.children:
            item.disabled = True
        try:
            await self.message.edit(view=self)
        except discord.HTTPException:
            pass


class ConfirmDedupeView(discord.ui.View):
    def __init__(self, owner_id: int, database_path):
        super().__init__(timeout=120)
        self.owner_id = owner_id
        self.database_path = database_path

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.owner_id

    @discord.ui.button(label="Remover duplicados", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button) -> None:
        report = await asyncio.to_thread(remove_duplicates, self.database_path)
        await interaction.response.edit_message(
            content=(
                f"✅ {report['removed']} duplicados removidos, "
                f"{report['transferred']} posses/wishes transferidas, "
                f"{report['skipped']} grupos mantidos por conflito de dono."
            ),
            view=None,
        )
        self.stop()

    @discord.ui.button(label="Cancelar", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button) -> None:
        await interaction.response.edit_message(content="Nada foi alterado.", view=None)
        self.stop()


class AdminCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.database_path = getattr(bot, "database_path", DATABASE_PATH)

    async def cog_check(self, context: commands.Context) -> bool:
        if not is_admin(context.author.id):
            await context.reply(
                "Este comando é exclusivo da conta administrativa do MEMI.", mention_author=False
            )
            return False
        return True

    @commands.group(name="admin", invoke_without_command=True, case_insensitive=True)
    async def admin(self, context: commands.Context) -> None:
        """Painel dos comandos de admin, com um botão por categoria."""
        view = AdminPanel(context.author)
        view.message = await context.reply(
            embed=admin_page("inicio"), view=view, mention_author=False
        )

    @admin.command(name="testeamistoso")
    async def test_friendly(self, context: commands.Context) -> None:
        winner = await _play_final(
            lambda embed: context.reply(embed=embed, mention_author=False),
            ("Teste 80", 80),
            ("Teste 75", 75),
            random.SystemRandom(),
            "Amistoso de teste",
        )
        await context.send(f"🏆 Vencedor do teste: **{winner}**")

    @admin.command(name="testecampeonato")
    async def test_championship(self, context: commands.Context, teams: int = 6) -> None:
        if not 2 <= teams <= 30:
            await context.reply("Informe entre 2 e 30 times.", mention_author=False)
            return
        rng = random.SystemRandom()
        ratings = {
            f"Teste {index + 1}": TEST_RATINGS[index % len(TEST_RATINGS)] for index in range(teams)
        }
        table = {name: {"pts": 0, "gf": 0, "ga": 0} for name in ratings}
        names = list(ratings)
        results = []
        for home_index, home in enumerate(names):
            for away in names[home_index + 1 :]:
                result = simulate_match(
                    home,
                    _test_lineup(ratings[home], "H"),
                    away,
                    _test_lineup(ratings[away], "A"),
                    rng=rng,
                )
                results.append(f"{home} {result.home_goals} × {result.away_goals} {away}")
                for name, scored, conceded in (
                    (home, result.home_goals, result.away_goals),
                    (away, result.away_goals, result.home_goals),
                ):
                    table[name]["gf"] += scored
                    table[name]["ga"] += conceded
                    table[name]["pts"] += (
                        3 if scored > conceded else (1 if scored == conceded else 0)
                    )
        ranking = sorted(
            table.items(),
            key=lambda item: (
                -item[1]["pts"],
                -(item[1]["gf"] - item[1]["ga"]),
                -item[1]["gf"],
                item[0],
            ),
        )
        description = "\n".join(
            f"`{index}.` {name} ({ratings[name]}) — **{row['pts']} pts** · SG {row['gf'] - row['ga']}"
            for index, (name, row) in enumerate(ranking, 1)
        )
        embed = discord.Embed(
            title=f"Teste de campeonato ({teams} times)",
            description=description[:4000],
            color=discord.Color.red(),
        )
        if len(results) <= 15:
            embed.add_field(name="Jogos", value="\n".join(results)[:1024], inline=False)
        embed.set_footer(text="A final entre os dois primeiros começa agora.")
        await context.reply(embed=embed, mention_author=False)
        (first, _), (second, _) = ranking[0], ranking[1]
        winner = await _play_final(
            lambda final_embed: context.send(embed=final_embed),
            (first, ratings[first]),
            (second, ratings[second]),
            rng,
            "FINAL de teste",
        )
        await context.send(f"🏆 Campeão do teste: **{winner}**")

    @commands.command(name="testeamistoso", hidden=True)
    async def test_friendly_shortcut(self, context: commands.Context) -> None:
        await self.test_friendly(context)

    @commands.command(name="testecampeonato", hidden=True)
    async def test_championship_shortcut(self, context: commands.Context, teams: int = 6) -> None:
        await self.test_championship(context, teams)

    @admin.command(name="testeimagem")
    async def test_image(self, context: commands.Context) -> None:
        """Manda a mesma escalação de três jeitos, para comparar o tamanho no Discord."""
        from src.squad_image import render_squad
        from src.teams import get_lineup, get_team

        team = get_team(context.author.id, self.database_path)
        lineup = get_lineup(context.author.id, self.database_path) if team else []
        if team is None:
            team = {"team_name": "Time de teste", "color": "#2E8B57", "formation": "4-3-3"}
        image = (await asyncio.to_thread(render_squad, team, lineup)).getvalue()

        def file() -> discord.File:
            return discord.File(BytesIO(image), filename="escalacao.png")

        embed = discord.Embed(title="1 · Dentro de embed", color=discord.Color.blurple())
        embed.set_image(url="attachment://escalacao.png")
        await context.send(embed=embed, file=file())
        await context.send("**2 · Anexo solto** (sem embed)", file=file())
        layout = discord.ui.LayoutView()
        layout.add_item(
            discord.ui.Container(
                discord.ui.TextDisplay("**3 · Components V2** (galeria de mídia)"),
                discord.ui.MediaGallery(discord.MediaGalleryItem("attachment://escalacao.png")),
            )
        )
        await context.send(view=layout, file=file())

    @commands.command(name="testeimagem", hidden=True)
    async def test_image_shortcut(self, context: commands.Context) -> None:
        await self.test_image(context)

    # Contas ----------------------------------------------------------------------------

    async def _memilets(self, context, member: discord.Member, amount: int) -> None:
        change, balance = await asyncio.to_thread(
            admin_tools.adjust_memilets,
            member.id,
            member.display_name,
            amount,
            self.database_path,
        )
        verb = "recebeu" if change >= 0 else "perdeu"
        await context.reply(
            f"{'💰' if change >= 0 else '💸'} {member.mention} {verb} **{abs(change):,}** "
            f"Memilets. Saldo: **{balance:,}**.".replace(",", "."),
            mention_author=False,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @admin.command(name="dar", aliases=["memilets", "pagar"])
    async def give_memilets(self, context, member: discord.Member, amount: int) -> None:
        """Dá Memilets (compensação, prêmio de evento...)."""
        if amount <= 0:
            await context.reply("Use um valor positivo.", mention_author=False)
            return
        await self._memilets(context, member, amount)

    @admin.command(name="tirar", aliases=["remover"])
    async def take_memilets(self, context, member: discord.Member, amount: int) -> None:
        """Tira Memilets (o saldo não fica negativo)."""
        if amount <= 0:
            await context.reply("Use um valor positivo.", mention_author=False)
            return
        await self._memilets(context, member, -amount)

    @admin.command(name="item", aliases=["itens", "caixa"])
    async def give_item(self, context, member: discord.Member, item: str, amount: int = 1) -> None:
        """Dá (ou tira, com número negativo) itens do inventário."""
        try:
            key = admin_tools.item_key(item)
        except AdminError as error:
            await context.reply(str(error), mention_author=False)
            return
        if not amount:
            await context.reply("Use uma quantidade diferente de zero.", mention_author=False)
            return
        left = await asyncio.to_thread(
            admin_tools.adjust_items,
            member.id,
            member.display_name,
            key,
            amount,
            self.database_path,
        )
        emoji, label = INVENTORY_ITEMS[key]
        verb = "recebeu" if amount > 0 else "perdeu"
        await context.reply(
            f"{emoji} {member.mention} {verb} {abs(amount)}× {label}. Agora tem **{left}**.",
            mention_author=False,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @admin.command(name="jogador", aliases=["player", "darjogador"])
    async def give_player(self, context, member: discord.Member, *, query: str) -> None:
        """Entrega um jogador (nome ou ID). Com `forçar` no fim, tira de quem já tem."""
        words = query.split()
        force = bool(words) and words[-1].casefold() in {"forçar", "forcar", "force"}
        if force:
            query = " ".join(words[:-1])
        try:
            given = await asyncio.to_thread(
                admin_tools.give_player,
                query,
                member.id,
                member.display_name,
                self.database_path,
                force=force,
            )
        except AdminError as error:
            await context.reply(
                str(error), mention_author=False, allowed_mentions=discord.AllowedMentions.none()
            )
            return
        player = given.player
        name = player.get("display_name") or player["name"]
        origin = f" (tirado de <@{given.previous_owner}>)" if given.previous_owner else ""
        await context.reply(
            f"🤝 **{name}** ({player['overall']}, {player.get('game_position')}) agora é de "
            f"{member.mention}{origin}.",
            mention_author=False,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @admin.command(name="resetar", aliases=["reset"])
    async def reset(self, context, member: discord.Member, what: str = "tudo") -> None:
        """Devolve os rolls do ciclo e/ou libera o claim na hora."""
        choice = what.casefold()
        if choice not in {"rolls", "roll", "claim", "tudo", "all"}:
            await context.reply("Use `rolls`, `claim` ou `tudo`.", mention_author=False)
            return
        done = []
        if choice in {"rolls", "roll", "tudo", "all"}:
            await asyncio.to_thread(admin_tools.reset_rolls, member.id, self.database_path)
            done.append("rolls do ciclo devolvidos")
        if choice in {"claim", "tudo", "all"}:
            await asyncio.to_thread(admin_tools.reset_claim, member.id, self.database_path)
            done.append("claim liberado")
        await context.reply(
            f"🔄 {member.mention}: " + " e ".join(done) + ".",
            mention_author=False,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    # Bot ----------------------------------------------------------------------------------

    @admin.command(name="status", aliases=["info", "versao", "versão"])
    async def status(self, context) -> None:
        """Versão rodando, tempo ligado, ping, tamanho do banco e último backup."""
        backup = admin_tools.last_backup(self.database_path)
        latency = getattr(self.bot, "latency", None)
        lines = [
            f"Versão: `{await asyncio.to_thread(_version)}`",
            f"Ligado há: **{_duration(time.monotonic() - STARTED)}**",
            "Ping: " + (f"**{latency * 1000:.0f} ms**" if latency else "—"),
            f"Servidores: **{len(getattr(self.bot, 'guilds', []) or [])}**",
            f"Banco do jogo: **{_size(admin_tools.database_size(self.database_path))}**",
            "Último backup: " + (f"`{backup.name}`" if backup else "nenhum ainda"),
        ]
        embed = discord.Embed(
            title="🛠️ MEMI · status", description="\n".join(lines), color=discord.Color.red()
        )
        await context.reply(embed=embed, mention_author=False)

    @admin.command(name="backup")
    async def backup(self, context) -> None:
        """Cópia do banco do jogo agora (guarda as últimas 10)."""
        path = await asyncio.to_thread(admin_tools.backup_database, self.database_path)
        await context.reply(
            f"💾 Backup feito: `{path.name}` ({_size(path.stat().st_size)}), na pasta "
            f"`{path.parent}`.",
            mention_author=False,
        )

    @admin.command(name="duplicados", aliases=["duplicatas"])
    async def duplicates(self, context: commands.Context) -> None:
        groups = await asyncio.to_thread(find_duplicates, self.database_path)
        if not groups:
            await context.reply("Nenhum jogador duplicado encontrado. ✅", mention_author=False)
            return
        lines = []
        for group in groups[:15]:
            keep, *drop = group["players"]
            lines.append(
                f"**{keep['name']}** — mantém ID {keep['id']} ({keep['overall']}), remove "
                + ", ".join(f"ID {player['id']} ({player['overall']})" for player in drop)
                + (" ⚠️ conflito de dono" if group["conflict"] else "")
            )
        embed = discord.Embed(
            title=f"{len(groups)} grupos de duplicados",
            description="\n".join(lines)[:4000],
            color=discord.Color.orange(),
        )
        embed.set_footer(
            text="Mantém o de maior overall; posse, bolas e wishes passam para ele. "
            "Grupos com donos diferentes não são alterados."
        )
        await context.reply(
            embed=embed,
            view=ConfirmDedupeView(context.author.id, self.database_path),
            mention_author=False,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AdminCog(bot))
