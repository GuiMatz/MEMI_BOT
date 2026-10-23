"""Cassino do FUT.AE (`mm!cassino`, `mm!apostar`, `mm!bet`).

Um painel com a aposta (½, ×2, valor digitado) e quatro jogos justos: cara ou coroa,
caça-níquel, roleta e minas. A aposta sai do saldo quando o jogo começa e o prêmio entra
na hora. As regras e as contas ficam em `src.casino`; as animações em `src.casino_art`.
"""

from __future__ import annotations

import asyncio
from io import BytesIO

import discord
from discord.ext import commands

from src import icons
from src.casino import (
    MAX_BET,
    MIN_BET,
    MINES_CELLS,
    MINES_OPTIONS,
    RED_NUMBERS,
    ROULETTE_BETS,
    SLOT_PAIR_MULTIPLIER,
    CasinoError,
    MinesGame,
    credit,
    flip_coin,
    new_rng,
    payout,
    place_bet,
    roulette_wins,
    spin_roulette,
    spin_slots,
    slots_multiplier,
)
from src.casino_art import coin_gif, roulette_gif, slots_gif
from src.db import DATABASE_PATH
from src.game import get_wallet, register_user
from src.wagers import parse_wager

CASINO_INTRO = (
    "Jogos **justos**: o retorno médio é 100% do que você aposta, a casa não fica com "
    "nada. Dá para ganhar (e perder) bastante no curto prazo. Escolha o valor e o jogo."
)
DEFAULT_BET = 1_000
SLOT_LABELS = {
    "cereja": "🍒",
    "limao": "🍋",
    "sino": "🔔",
    "estrela": "⭐",
    "diamante": "💎",
    "sete": "7️⃣",
}


def _money(value: int) -> str:
    return f"{value:,}".replace(",", ".")


def _multiplier(value) -> str:
    return f"{float(value):.2f}".rstrip("0").rstrip(".").replace(".", ",") + "x"


def result_embed(title: str, bet: int, prize: int, balance: int, detail: str) -> discord.Embed:
    won = prize > 0
    net = prize - bet
    embed = discord.Embed(
        title=title,
        description=detail,
        color=(
            discord.Color.from_rgb(88, 196, 120)
            if net >= 0 and won
            else discord.Color.from_rgb(214, 84, 84)
        ),
    )
    embed.add_field(name="Apostou", value=icons.memilets(bet))
    embed.add_field(
        name="Ganhou" if won else "Perdeu",
        value=icons.memilets(prize) if won else icons.memilets(bet),
    )
    embed.add_field(name="Saldo", value=f"{icons.memilets(balance)} Memilets")
    return embed


class CasinoView(discord.ui.View):
    """O painel: valor da aposta e os botões dos jogos."""

    def __init__(self, cog: "CasinoCog", owner, bet: int):
        super().__init__(timeout=600)
        self.cog = cog
        self.owner = owner
        self.bet = bet

    def embed(self) -> discord.Embed:
        balance = get_wallet(self.owner.id, self.cog.database_path)["memilets"]
        embed = discord.Embed(
            title="🎰 Cassino", description=f"*{CASINO_INTRO}*", color=discord.Color.gold()
        )
        embed.add_field(name="Aposta", value=f"{icons.memilets(self.bet)} Memilets")
        embed.add_field(name="Saldo", value=f"{icons.memilets(balance)} Memilets")
        embed.add_field(
            name="Jogos",
            value=(
                "🪙 **Cara ou coroa** · acertou, 2x\n"
                "🎰 **Caça-níquel** · três iguais até 250x, dois iguais "
                f"{_multiplier(SLOT_PAIR_MULTIPLIER)}\n"
                "🎡 **Roleta** · cor ou par/ímpar 2x, dúzia 3x, número 36x\n"
                "💣 **Minas** · cada casa segura aumenta o prêmio; saque quando quiser"
            ),
            inline=False,
        )
        embed.set_footer(text=f"Aposta de {_money(MIN_BET)} a {_money(MAX_BET)} Memilets")
        return embed

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner.id:
            await interaction.response.send_message(
                "Esse cassino é de quem abriu. Use `mm!cassino` para o seu.", ephemeral=True
            )
            return False
        return True

    async def _refresh(self, interaction: discord.Interaction) -> None:
        await interaction.response.edit_message(embed=self.embed(), view=self)

    @discord.ui.button(label="½", style=discord.ButtonStyle.secondary, row=0)
    async def half(self, interaction: discord.Interaction, button) -> None:
        self.bet = max(MIN_BET, self.bet // 2)
        await self._refresh(interaction)

    @discord.ui.button(label="×2", style=discord.ButtonStyle.secondary, row=0)
    async def double(self, interaction: discord.Interaction, button) -> None:
        self.bet = min(MAX_BET, self.bet * 2)
        await self._refresh(interaction)

    @discord.ui.button(label="Valor…", emoji="✏️", style=discord.ButtonStyle.secondary, row=0)
    async def custom(self, interaction: discord.Interaction, button) -> None:
        await interaction.response.send_modal(BetModal(self))

    @discord.ui.button(label="Cara ou coroa", emoji="🪙", style=discord.ButtonStyle.primary, row=1)
    async def coin(self, interaction: discord.Interaction, button) -> None:
        await interaction.response.send_message(
            f"🪙 Aposta de {icons.memilets(self.bet)}: escolha o lado.",
            view=CoinView(self.cog, self.owner, self.bet),
        )

    @discord.ui.button(label="Caça-níquel", emoji="🎰", style=discord.ButtonStyle.primary, row=1)
    async def slots(self, interaction: discord.Interaction, button) -> None:
        await self.cog.play_slots(interaction, self.bet)

    @discord.ui.button(label="Roleta", emoji="🎡", style=discord.ButtonStyle.primary, row=1)
    async def roulette(self, interaction: discord.Interaction, button) -> None:
        await interaction.response.send_message(
            f"🎡 Aposta de {icons.memilets(self.bet)}: escolha onde apostar.",
            view=RouletteView(self.cog, self.owner, self.bet),
        )

    @discord.ui.button(label="Minas", emoji="💣", style=discord.ButtonStyle.primary, row=1)
    async def mines(self, interaction: discord.Interaction, button) -> None:
        await interaction.response.send_message(
            f"💣 Aposta de {icons.memilets(self.bet)}: quantas bombas na grade de "
            f"{MINES_CELLS}? Mais bombas, prêmio maior a cada casa.",
            view=MinesSetupView(self.cog, self.owner, self.bet),
        )


class BetModal(discord.ui.Modal, title="Valor da aposta"):
    amount = discord.ui.TextInput(label="Quantos Memilets?", placeholder="ex.: 5000 ou 5k")

    def __init__(self, view: CasinoView):
        super().__init__()
        self.view = view

    async def on_submit(self, interaction: discord.Interaction) -> None:
        value = parse_wager(str(self.amount.value))
        if value is None or not MIN_BET <= value <= MAX_BET:
            await interaction.response.send_message(
                f"Digite um valor de {_money(MIN_BET)} a {_money(MAX_BET)}.", ephemeral=True
            )
            return
        self.view.bet = value
        await interaction.response.edit_message(embed=self.view.embed(), view=self.view)


class OwnerView(discord.ui.View):
    def __init__(self, cog, owner, bet: int, timeout: float = 180):
        super().__init__(timeout=timeout)
        self.cog = cog
        self.owner = owner
        self.bet = bet

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner.id:
            await interaction.response.send_message(
                "Essa aposta é de outra pessoa. Use `mm!cassino`.", ephemeral=True
            )
            return False
        return True


class CoinView(OwnerView):
    @discord.ui.button(label="Cara", emoji="🙂", style=discord.ButtonStyle.primary)
    async def heads(self, interaction: discord.Interaction, button) -> None:
        self.stop()
        await self.cog.play_coin(interaction, self.bet, "cara")

    @discord.ui.button(label="Coroa", emoji="👑", style=discord.ButtonStyle.primary)
    async def tails(self, interaction: discord.Interaction, button) -> None:
        self.stop()
        await self.cog.play_coin(interaction, self.bet, "coroa")


class RouletteView(OwnerView):
    def __init__(self, cog, owner, bet: int):
        super().__init__(cog, owner, bet)
        select = discord.ui.Select(
            placeholder="Onde apostar?",
            options=[
                discord.SelectOption(label=f"{label} · paga {multiplier}x", value=key)
                for key, (label, multiplier) in ROULETTE_BETS.items()
            ],
        )
        select.callback = self._chosen
        self.add_item(select)

    async def _chosen(self, interaction: discord.Interaction) -> None:
        choice = interaction.data["values"][0]
        if choice == "numero":
            await interaction.response.send_modal(NumberModal(self))
            return
        self.stop()
        await self.cog.play_roulette(interaction, self.bet, choice, None)


class NumberModal(discord.ui.Modal, title="Número da roleta"):
    number = discord.ui.TextInput(label="Número de 1 a 36", max_length=2)

    def __init__(self, view: RouletteView):
        super().__init__()
        self.view = view

    async def on_submit(self, interaction: discord.Interaction) -> None:
        text = str(self.number.value).strip()
        if not text.isdigit() or not 1 <= int(text) <= 36:
            await interaction.response.send_message("Escolha um número de 1 a 36.", ephemeral=True)
            return
        self.view.stop()
        await self.view.cog.play_roulette(interaction, self.view.bet, "numero", int(text))


class MinesSetupView(OwnerView):
    def __init__(self, cog, owner, bet: int):
        super().__init__(cog, owner, bet)
        for mines in MINES_OPTIONS:
            button = discord.ui.Button(
                label=f"{mines} bomba{'s' if mines > 1 else ''}",
                style=discord.ButtonStyle.secondary,
            )
            button.callback = self._starter(mines)
            self.add_item(button)

    def _starter(self, mines: int):
        async def start(interaction: discord.Interaction) -> None:
            self.stop()
            await self.cog.start_mines(interaction, self.bet, mines)

        return start


class MinesView(OwnerView):
    """A grade 4×4: cada casa é um botão; "Sacar" leva o prêmio atual."""

    def __init__(self, cog, owner, bet: int, game: MinesGame):
        super().__init__(cog, owner, bet, timeout=300)
        self.game = game
        self.over = False
        self._build()

    def _build(self, reveal: bool = False) -> None:
        self.clear_items()
        for cell in range(MINES_CELLS):
            opened = cell in self.game.opened
            bomb = cell in self.game.bombs
            if opened:
                button = discord.ui.Button(
                    emoji="💎", style=discord.ButtonStyle.success, disabled=True
                )
            elif reveal and bomb:
                button = discord.ui.Button(
                    emoji="💣", style=discord.ButtonStyle.danger, disabled=True
                )
            else:
                button = discord.ui.Button(
                    label="​", style=discord.ButtonStyle.secondary, disabled=reveal
                )
            button.row = cell // 4
            button.callback = self._opener(cell)
            self.add_item(button)
        prize = payout(self.bet, self.game.multiplier)
        cash = discord.ui.Button(
            label=f"Sacar {_money(prize)} ({_multiplier(self.game.multiplier)})",
            emoji="💰",
            style=discord.ButtonStyle.primary,
            disabled=reveal or not self.game.opened,
            row=4,
        )
        cash.callback = self._cash_out
        self.add_item(cash)

    def text(self) -> str:
        nxt = _multiplier(self.game.next_multiplier()) if self.game.safe_left else "—"
        return (
            f"💣 **Minas** · {self.game.mines} bomba(s) · aposta {icons.memilets(self.bet)}\n"
            f"Agora: **{_multiplier(self.game.multiplier)}** · próxima casa: **{nxt}**"
        )

    def _opener(self, cell: int):
        async def open_cell(interaction: discord.Interaction) -> None:
            if self.over:
                await interaction.response.defer()
                return
            if self.game.open(cell):
                if self.game.safe_left == 0:
                    await self._finish(interaction, cashed=True)
                    return
                self._build()
                await interaction.response.edit_message(content=self.text(), view=self)
                return
            await self._finish(interaction, cashed=False)

        return open_cell

    async def _cash_out(self, interaction: discord.Interaction) -> None:
        await self._finish(interaction, cashed=True)

    async def _finish(self, interaction: discord.Interaction | None, cashed: bool) -> None:
        if self.over:
            return
        self.over = True
        self.stop()
        prize = payout(self.bet, self.game.multiplier) if cashed else 0
        balance = await asyncio.to_thread(credit, self.owner.id, prize, self.cog.database_path)
        self._build(reveal=True)
        line = (
            f"💰 Sacou **{_money(prize)}** ({_multiplier(self.game.multiplier)})"
            if cashed
            else "💥 **BOOM!** Achou uma bomba e perdeu a aposta."
        )
        content = f"{self.text()}\n{line} · saldo {_money(balance)}"
        if interaction is not None:
            await interaction.response.edit_message(content=content, view=self)
        elif self.message is not None:
            await self.message.edit(content=content, view=self)

    async def on_timeout(self) -> None:
        # Deixou o jogo aberto: saca o que já tinha ganho (nunca perde por sumir).
        await self._finish(None, cashed=True)


class CasinoCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.database_path = getattr(bot, "database_path", DATABASE_PATH)
        self.rng = new_rng()

    @commands.command(name="cassino", aliases=["casino", "apostar", "bet"])
    async def casino(self, context: commands.Context, amount: str | None = None) -> None:
        """`mm!cassino [valor]`: o painel com os jogos de aposta."""
        register_user(context.author.id, context.author.display_name, self.database_path)
        bet = parse_wager(amount) if amount else DEFAULT_BET
        bet = min(MAX_BET, max(MIN_BET, bet or DEFAULT_BET))
        view = CasinoView(self, context.author, bet)
        await context.reply(embed=view.embed(), view=view, mention_author=False)

    async def _charge(self, interaction: discord.Interaction, bet: int) -> bool:
        try:
            await asyncio.to_thread(
                place_bet,
                interaction.user.id,
                interaction.user.display_name,
                bet,
                self.database_path,
            )
        except CasinoError as error:
            if interaction.response.is_done():
                await interaction.followup.send(str(error), ephemeral=True)
            else:
                await interaction.response.send_message(str(error), ephemeral=True)
            return False
        return True

    async def _show(self, interaction, embed: discord.Embed, gif: bytes, *, edit: bool) -> None:
        embed.set_image(url="attachment://cassino.gif")
        file = discord.File(BytesIO(gif), "cassino.gif")
        if edit:
            await interaction.edit_original_response(
                content=None, embed=embed, attachments=[file], view=None
            )
        else:
            await interaction.followup.send(embed=embed, file=file)

    async def play_coin(self, interaction: discord.Interaction, bet: int, side: str) -> None:
        if not await self._charge(interaction, bet):
            return
        await interaction.response.defer()
        result = flip_coin(self.rng)
        won = result == side
        prize = bet * 2 if won else 0
        balance = await asyncio.to_thread(credit, interaction.user.id, prize, self.database_path)
        headline = f"{result.upper()} · " + (f"+{_money(prize)}" if won else "PERDEU")
        gif = await asyncio.to_thread(coin_gif, result, headline, won)
        embed = result_embed(
            "🪙 Cara ou coroa", bet, prize, balance, f"Você pediu **{side}**, deu **{result}**."
        )
        await self._show(interaction, embed, gif, edit=True)

    async def play_slots(self, interaction: discord.Interaction, bet: int) -> None:
        if not await self._charge(interaction, bet):
            return
        await interaction.response.defer()
        reels = spin_slots(self.rng)
        multiplier = slots_multiplier(reels)
        prize = payout(bet, multiplier)
        balance = await asyncio.to_thread(credit, interaction.user.id, prize, self.database_path)
        headline = f"{_multiplier(multiplier)} · +{_money(prize)}" if prize else "NÃO FOI DESSA VEZ"
        gif = await asyncio.to_thread(slots_gif, reels, headline, bool(prize))
        line = " ".join(SLOT_LABELS[reel] for reel in reels)
        embed = result_embed(
            "🎰 Caça-níquel", bet, prize, balance, f"{line} · {_multiplier(multiplier)}"
        )
        await self._show(interaction, embed, gif, edit=False)

    async def play_roulette(self, interaction, bet: int, choice: str, number: int | None) -> None:
        if not await self._charge(interaction, bet):
            return
        await interaction.response.defer()
        drawn = spin_roulette(self.rng)
        label, multiplier = ROULETTE_BETS[choice]
        won = roulette_wins(choice, drawn, number)
        prize = bet * multiplier if won else 0
        balance = await asyncio.to_thread(credit, interaction.user.id, prize, self.database_path)
        color = "VERMELHO" if drawn in RED_NUMBERS else "PRETO"
        headline = f"{drawn} {color} · " + (f"+{_money(prize)}" if won else "PERDEU")
        gif = await asyncio.to_thread(roulette_gif, drawn, headline, won)
        bet_text = f"número {number}" if choice == "numero" else label
        embed = result_embed(
            "🎡 Roleta",
            bet,
            prize,
            balance,
            f"Você apostou em **{bet_text}**; saiu **{drawn} {color.lower()}**.",
        )
        await self._show(interaction, embed, gif, edit=True)

    async def start_mines(self, interaction: discord.Interaction, bet: int, mines: int) -> None:
        if not await self._charge(interaction, bet):
            return
        game = MinesGame.new(mines, self.rng)
        view = MinesView(self, interaction.user, bet, game)
        await interaction.response.edit_message(content=view.text(), view=view)
        view.message = await interaction.original_response()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(CasinoCog(bot))
