"""Mercado entre pessoas: vender, doar e trocar jogadores.

Tudo mostra o resultado antes de acontecer:

- `mm!vender` abre o painel de venda: filtros de raridade e posição, "Selecionar todos"
  (mesmo mais de 25), proteger com 🔒, incluir titulares e a cotação ao vivo, tudo em
  botões. `mm!vender <nome>` vende um jogador direto (`mm!demitir` = alias).
- Venda rápida: `mm!vender base|comum|…`, `mm!vender abaixo 70` ou `mm!vender posicao ZAG`
  junta todos os reservas do filtro (sem limite de 25) e mostra o total antes.
- `mm!proteger <nome>` liga/desliga o 🔒: protegido não aparece em venda nenhuma.
- `mm!dar @pessoa <jogador | valor>` doa jogadores ou Memilets, com confirmação.
- `mm!troca @pessoa` abre um montador com menus dos dois elencos, busca por nome e
  Memilets dos dois lados; a outra pessoa aceita ou recusa num botão. O formato em
  texto (`mm!troca @pessoa A, B por C 100`) preenche o montador.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, Iterable
from typing import Any

import discord
from discord.ext import commands

from src import icons
from src.cogs.common import ConfirmView, PlayerChoiceView, ambiguity_text, player_label
from src.db import DATABASE_PATH, connect, normalize_player_name
from src.ratings import RARITY_TIERS, rarity_index
from src.game import list_owned_players, register_user, resolve_player
from src.teams import get_lineup
from src.transactions import (
    MAX_SALE_PLAYERS,
    MAX_TRADE_PLAYERS,
    TransactionError,
    cancel_trade,
    create_trade,
    give_players,
    market_values,
    player_warnings,
    sale_quote,
    sell_players,
    quick_sale_ids,
    set_protection,
    settle_trade,
    toggle_protection,
    trade_details,
)

OPTION_LIMIT = 25
SALE_LINES = 15  # jogadores listados na cotação; o resto vira "e mais N"
STATUS_TEXT = {
    "accepted": "✅ Troca concluída",
    "declined": "❌ Troca recusada",
    "expired": "⌛ Proposta expirada",
    "cancelled": "🚫 Proposta cancelada",
}


def coins(amount: int) -> str:
    return icons.memilets(amount)


def player_line(player: dict[str, Any], value: int | None = None) -> str:
    position = player.get("game_position") or "—"
    line = f"**{player.get('overall') or '—'}** {position} · {player_label(player)}"
    return f"{line} — {coins(value)}" if value is not None else line


def _squad_options(
    players: list[dict[str, Any]],
    selected: set[int],
    query: str,
    values: dict[int, int] | None = None,
) -> list[discord.SelectOption]:
    """Selecionados primeiro; depois os que batem com a busca, até 25 opções."""
    wanted = normalize_player_name(query) if query else ""
    chosen = [player for player in players if int(player["id"]) in selected]
    others = [
        player
        for player in players
        if int(player["id"]) not in selected
        and (not wanted or wanted in normalize_player_name(player_label(player)))
    ]
    options = []
    for player in (chosen + others)[:OPTION_LIMIT]:
        details = [player.get("game_position") or "—", player.get("club_name") or "Passe livre"]
        if values and int(player["id"]) in values:
            details.insert(1, f"vende por {values[int(player['id'])]:,}".replace(",", "."))
        options.append(
            discord.SelectOption(
                label=f"{player.get('overall') or '—'} · {player_label(player)}"[:100],
                description=" · ".join(details)[:100],
                value=str(player["id"]),
                default=int(player["id"]) in selected,
            )
        )
    return options


def _picker(
    players: list[dict[str, Any]],
    selected: set[int],
    query: str,
    *,
    placeholder: str,
    empty: str,
    max_values: int,
    row: int,
    values: dict[int, int] | None = None,
) -> discord.ui.Select:
    options = _squad_options(players, selected, query, values)
    if not options:
        return discord.ui.Select(
            placeholder=empty,
            options=[discord.SelectOption(label="—", value="none")],
            disabled=True,
            row=row,
        )
    shown = len(options)
    hint = f" ({shown} de {len(players)} · 🔎 para buscar)" if len(players) > shown else ""
    return discord.ui.Select(
        placeholder=(placeholder + hint)[:150],
        options=options,
        min_values=0,
        max_values=min(max_values, shown),
        row=row,
    )


class SearchModal(discord.ui.Modal):
    """Caixa de busca por nome; `on_search(interaction, texto)` aplica o filtro."""

    def __init__(
        self, title: str, on_search: Callable[[discord.Interaction, str], Awaitable[None]]
    ):
        super().__init__(title=title[:45])
        self.on_search = on_search
        self.query = discord.ui.TextInput(
            label="Nome do jogador (vazio mostra todos)", required=False, max_length=60
        )
        self.add_item(self.query)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.on_search(interaction, str(self.query.value or "").strip())


class OwnerView(discord.ui.View):
    """View que só quem abriu pode usar."""

    def __init__(self, owner_id: int, timeout: float = 600):
        super().__init__(timeout=timeout)
        self.owner_id = owner_id
        self.message: discord.Message | None = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Esse menu não é seu.", ephemeral=True)
            return False
        return True

    async def on_timeout(self) -> None:
        if self.message:
            try:
                await self.message.edit(view=None)
            except discord.HTTPException:
                pass


# --- Venda ------------------------------------------------------------------------------


def sale_embed(quote: dict[str, Any]) -> discord.Embed:
    players = quote["players"]
    lines = []
    for player in players[:SALE_LINES]:
        lines.append(player_line(player, player["value"]))
        lines.extend(f"└ ⚠️ {warning}" for warning in player["warnings"])
    if len(players) > SALE_LINES:
        lines.append(f"… e mais **{len(players) - SALE_LINES}** jogadores")
    bonus = round((quote["multiplier"] - 1) * 100)
    total = coins(quote["total"]) + (f" (inclui +{bonus}% do upgrade)" if bonus else "")
    embed = discord.Embed(
        title=f"💰 Vender {len(players)} jogador{'es' if len(players) > 1 else ''}?",
        description="\n".join(lines)[:4000],
        color=discord.Color.orange(),
    )
    embed.add_field(name="Você recebe", value=total, inline=True)
    embed.add_field(
        name="Saldo",
        value=f"{coins(quote['balance'])} → {coins(quote['balance_after'])}",
        inline=True,
    )
    embed.set_footer(text="A venda não pode ser desfeita: o jogador volta para o mercado.")
    return embed


async def _finish_sale(interaction: discord.Interaction, user_id: int, ids, database_path):
    try:
        total = sell_players(user_id, ids, database_path)
    except TransactionError as error:
        await interaction.response.edit_message(content=str(error), embed=None, view=None)
        return
    count = len(ids)
    await interaction.response.edit_message(
        content=(
            f"✅ {count} jogador{'es vendidos' if count > 1 else ' vendido'}. "
            f"Você recebeu {coins(total)}."
        ),
        embed=None,
        view=None,
    )


class SalePanelView(OwnerView):
    """Painel de venda: filtros, seleção em massa, proteção e cotação ao vivo, tudo em botões.

    Dois modos: **venda** (reservas vendáveis; titulares só se ligar "Incluir titulares")
    e **protegidos** (os 🔒, para tirar a proteção). "Selecionar todos" marca todos os do
    filtro, mesmo passando de 25; a cotação embaixo mostra total e saldo antes de vender.
    """

    def __init__(self, owner_id: int, database_path, *, preselect: Iterable[int] = ()):
        super().__init__(owner_id)
        self.database_path = database_path
        self.mode = "sell"
        self.rarity: int | None = None
        self.position: str | None = None
        self.include_starters = False
        self.query = ""
        self.notice = ""
        self.selected: set[int] = set()
        self._load()
        self.selected = {int(i) for i in preselect if int(i) in self.by_id}
        self._build()

    # Dados

    def _load(self) -> None:
        owned = list_owned_players(self.owner_id, self.database_path, limit=5000, sort_by="overall")
        owned.reverse()  # mais fracos primeiro: é quem costuma ir embora
        starters = {int(player["id"]) for player in get_lineup(self.owner_id, self.database_path)}
        for player in owned:
            player["starter"] = int(player["id"]) in starters
        self.owned = owned
        self.by_id = {int(player["id"]): player for player in owned}
        sellable = [p for p in owned if not p.get("locked")]
        self.values: dict[int, int] = {}
        if sellable:
            quote = sale_quote(self.owner_id, [p["id"] for p in sellable], self.database_path)
            self.values = {int(p["id"]): p["value"] for p in quote["players"]}

    def pool(self) -> list[dict[str, Any]]:
        """Jogadores do modo atual, antes dos filtros."""
        if self.mode == "protected":
            return [p for p in self.owned if p.get("locked")]
        return [
            p
            for p in self.owned
            if not p.get("locked") and (self.include_starters or not p["starter"])
        ]

    def filtered(self) -> list[dict[str, Any]]:
        players = self.pool()
        if self.rarity is not None:
            players = [p for p in players if rarity_index(int(p["overall"] or 1)) == self.rarity]
        if self.position:
            players = [p for p in players if p.get("game_position") == self.position]
        return players

    # Interface

    def _build(self) -> None:
        self.clear_items()
        pool = self.pool()
        valid = {int(p["id"]) for p in pool}
        self.selected &= valid
        tiers: dict[int, int] = {}
        positions: dict[str, int] = {}
        for player in pool:
            tier = rarity_index(int(player["overall"] or 1))
            tiers[tier] = tiers.get(tier, 0) + 1
            position = player.get("game_position") or "—"
            positions[position] = positions.get(position, 0) + 1

        rarity = discord.ui.Select(
            placeholder="Raridade: todas",
            options=[
                discord.SelectOption(
                    label="Todas as raridades", value="all", default=self.rarity is None
                )
            ]
            + [
                discord.SelectOption(
                    label=f"{RARITY_TIERS[tier][0]} ({count})",
                    value=str(tier),
                    emoji=RARITY_TIERS[tier][1],
                    default=self.rarity == tier,
                )
                for tier, count in sorted(tiers.items())
            ],
            row=0,
        )
        rarity.callback = self._rarity
        self.add_item(rarity)

        order = ("GOL", "ZAG", "LD", "LE", "MC", "MEI", "PE", "PD", "CA")
        position = discord.ui.Select(
            placeholder="Posição: todas",
            options=[
                discord.SelectOption(
                    label="Todas as posições", value="all", default=self.position is None
                )
            ]
            + [
                discord.SelectOption(
                    label=f"{name} ({positions[name]})", value=name, default=self.position == name
                )
                for name in sorted(
                    positions, key=lambda item: order.index(item) if item in order else 99
                )
            ],
            row=1,
        )
        position.callback = self._position
        self.add_item(position)

        players = self.filtered()
        select = _picker(
            players,
            self.selected,
            self.query,
            placeholder="Marque os jogadores" if self.mode == "sell" else "Marque quem desproteger",
            empty="Ninguém com esse filtro",
            max_values=OPTION_LIMIT,
            row=2,
            values=self.values if self.mode == "sell" else None,
        )
        select.callback = self._picked
        self.add_item(select)

        buttons = [
            ("Buscar", "🔎", self._search, False, discord.ButtonStyle.secondary),
            (
                f"Selecionar todos ({len(players)})",
                "☑️",
                self._select_all,
                not players,
                discord.ButtonStyle.secondary,
            ),
            ("Limpar", "✖️", self._clear, not self.selected, discord.ButtonStyle.secondary),
        ]
        if self.mode == "sell":
            buttons.append(
                (
                    "Titulares: " + ("incluídos" if self.include_starters else "fora"),
                    "⭐",
                    self._toggle_starters,
                    False,
                    discord.ButtonStyle.secondary,
                )
            )
            protected = sum(1 for p in self.owned if p.get("locked"))
            buttons.append(
                (
                    f"Protegidos ({protected})",
                    "🔒",
                    self._to_protected,
                    False,
                    discord.ButtonStyle.secondary,
                )
            )
        else:
            buttons.append(
                ("Voltar à venda", "↩️", self._to_sell, False, discord.ButtonStyle.secondary)
            )
        for label, emoji, callback, disabled, style in buttons:
            button = discord.ui.Button(
                label=label, emoji=emoji, style=style, disabled=disabled, row=3
            )
            button.callback = callback
            self.add_item(button)

        count = len(self.selected)
        if self.mode == "sell":
            total = sum(self.values.get(i, 0) for i in self.selected)
            sell = discord.ui.Button(
                label=f"Vender {count} por {total:,}".replace(",", ".") if count else "Vender",
                emoji="💰",
                style=discord.ButtonStyle.danger,
                disabled=not count,
                row=4,
            )
            sell.callback = self._sell
            self.add_item(sell)
            lock = discord.ui.Button(
                label=f"Proteger {count}" if count else "Proteger",
                emoji="🔒",
                disabled=not count,
                row=4,
            )
            lock.callback = self._lock
            self.add_item(lock)
        else:
            unlock = discord.ui.Button(
                label=f"Tirar proteção de {count}" if count else "Tirar proteção",
                emoji="🔓",
                style=discord.ButtonStyle.primary,
                disabled=not count,
                row=4,
            )
            unlock.callback = self._unlock
            self.add_item(unlock)
        close = discord.ui.Button(label="Fechar", row=4)
        close.callback = self._close
        self.add_item(close)

    def embed(self) -> discord.Embed:
        filters = []
        if self.rarity is not None:
            filters.append(f"{RARITY_TIERS[self.rarity][1]} {RARITY_TIERS[self.rarity][0]}")
        if self.position:
            filters.append(self.position)
        if self.query:
            filters.append(f"“{self.query}”")
        shown = f"Filtro: {' · '.join(filters)}" if filters else "Sem filtro"
        if self.mode == "protected":
            embed = discord.Embed(
                title="🔒 Jogadores protegidos",
                description=(
                    "Protegidos não aparecem em venda nenhuma. Marque quem deve voltar a "
                    f"poder ser vendido.\n{shown} · {len(self.filtered())} jogador(es)"
                ),
                color=discord.Color.dark_teal(),
            )
        elif self.selected:
            embed = sale_embed(sale_quote(self.owner_id, self.selected, self.database_path))
            embed.description = f"{shown}\n\n{embed.description}"
        else:
            hidden = []
            if not self.include_starters:
                hidden.append("titulares")
            hidden.append("🔒 protegidos")
            embed = discord.Embed(
                title="💰 Vender jogadores",
                description=(
                    "Filtre por raridade e posição, marque no menu ou use **Selecionar todos**. "
                    "O total e o saldo depois aparecem aqui antes de vender.\n"
                    f"{shown} · {len(self.filtered())} jogador(es) · fora da lista: "
                    + ", ".join(hidden)
                    + "."
                ),
                color=discord.Color.orange(),
            )
        if self.notice:
            embed.add_field(name="​", value=self.notice, inline=False)
        return embed

    async def _refresh(self, interaction: discord.Interaction) -> None:
        self._build()
        try:
            embed = self.embed()
        except TransactionError as error:
            self.selected.clear()
            self._build()
            embed = self.embed()
            embed.set_footer(text=str(error))
        self.notice = ""
        await interaction.response.edit_message(content=None, embed=embed, view=self)

    # Filtros e seleção

    async def _rarity(self, interaction: discord.Interaction) -> None:
        value = interaction.data["values"][0]
        self.rarity = None if value == "all" else int(value)
        await self._refresh(interaction)

    async def _position(self, interaction: discord.Interaction) -> None:
        value = interaction.data["values"][0]
        self.position = None if value == "all" else value
        await self._refresh(interaction)

    async def _picked(self, interaction: discord.Interaction) -> None:
        values = {int(value) for value in interaction.data.get("values", [])}
        visible = {
            int(option.value)
            for option in _squad_options(self.filtered(), self.selected, self.query, self.values)
        }
        self.selected = (self.selected - visible) | values
        await self._refresh(interaction)

    async def _search(self, interaction: discord.Interaction) -> None:
        async def apply(modal_interaction: discord.Interaction, text: str) -> None:
            self.query = text
            await self._refresh(modal_interaction)

        await interaction.response.send_modal(SearchModal("Buscar no seu elenco", apply))

    async def _select_all(self, interaction: discord.Interaction) -> None:
        """Marca todos os do filtro (raridade, posição e busca), mesmo passando de 25."""
        wanted = normalize_player_name(self.query) if self.query else ""
        chosen = [
            int(p["id"])
            for p in self.filtered()
            if not wanted or wanted in normalize_player_name(player_label(p))
        ]
        if self.mode == "sell" and len(self.selected | set(chosen)) > MAX_SALE_PLAYERS:
            chosen = chosen[: max(0, MAX_SALE_PLAYERS - len(self.selected))]
            self.notice = f"⚠️ Dá para vender até {MAX_SALE_PLAYERS} por vez; marquei os primeiros."
        self.selected |= set(chosen)
        await self._refresh(interaction)

    async def _clear(self, interaction: discord.Interaction) -> None:
        self.selected.clear()
        await self._refresh(interaction)

    async def _toggle_starters(self, interaction: discord.Interaction) -> None:
        self.include_starters = not self.include_starters
        await self._refresh(interaction)

    async def _to_protected(self, interaction: discord.Interaction) -> None:
        self.mode, self.selected, self.rarity, self.position, self.query = (
            "protected",
            set(),
            None,
            None,
            "",
        )
        await self._refresh(interaction)

    async def _to_sell(self, interaction: discord.Interaction) -> None:
        self.mode, self.selected, self.rarity, self.position, self.query = (
            "sell",
            set(),
            None,
            None,
            "",
        )
        await self._refresh(interaction)

    # Ações

    async def _lock(self, interaction: discord.Interaction) -> None:
        changed = set_protection(self.owner_id, self.selected, True, self.database_path)
        self.selected.clear()
        self._load()
        self.notice = f"🔒 {changed} jogador(es) protegido(s): saíram da lista de venda."
        await self._refresh(interaction)

    async def _unlock(self, interaction: discord.Interaction) -> None:
        changed = set_protection(self.owner_id, self.selected, False, self.database_path)
        self.selected.clear()
        self._load()
        self.notice = f"🔓 {changed} jogador(es) sem proteção: voltaram para a venda."
        await self._refresh(interaction)

    async def _sell(self, interaction: discord.Interaction) -> None:
        self.stop()
        await _finish_sale(interaction, self.owner_id, list(self.selected), self.database_path)

    async def _close(self, interaction: discord.Interaction) -> None:
        self.stop()
        await interaction.response.edit_message(
            content="Painel de venda fechado.", embed=None, view=None
        )


# --- Troca ------------------------------------------------------------------------------


class TradeBuilderView(OwnerView):
    """Monta a proposta: jogadores e Memilets dos dois lados, com valores à vista."""

    def __init__(self, author: discord.abc.User, target: discord.abc.User, database_path):
        super().__init__(author.id)
        self.author = author
        self.target = target
        self.database_path = database_path
        self.mine = list_owned_players(author.id, database_path, limit=5000, sort_by="overall")
        self.theirs = list_owned_players(target.id, database_path, limit=5000, sort_by="overall")
        self.give: set[int] = set()
        self.take: set[int] = set()
        self.pay = 0
        self.ask = 0
        self.queries = {"mine": "", "theirs": ""}
        self._build()

    def prefill(self, give: list[int], take: list[int], pay: int, ask: int) -> None:
        self.give, self.take, self.pay, self.ask = set(give), set(take), pay, ask
        self._build()

    def _build(self) -> None:
        self.clear_items()
        mine = _picker(
            self.mine,
            self.give,
            self.queries["mine"],
            placeholder="Você dá…",
            empty="Seu elenco está vazio",
            max_values=MAX_TRADE_PLAYERS,
            row=0,
        )
        mine.callback = self._pick_mine
        self.add_item(mine)
        theirs = _picker(
            self.theirs,
            self.take,
            self.queries["theirs"],
            placeholder=f"Você pede de {self.target.display_name}…",
            empty=f"O elenco de {self.target.display_name} está vazio",
            max_values=MAX_TRADE_PLAYERS,
            row=1,
        )
        theirs.callback = self._pick_theirs
        self.add_item(theirs)
        for label, side, row in (("Buscar no meu", "mine", 2), ("Buscar no outro", "theirs", 2)):
            button = discord.ui.Button(label=label, emoji="🔎", row=row)
            button.callback = self._searcher(side)
            self.add_item(button)
        money = discord.ui.Button(label="Memilets", emoji="🪙", row=2)
        money.callback = self._money
        self.add_item(money)
        send = discord.ui.Button(
            label="Enviar proposta",
            emoji="📨",
            style=discord.ButtonStyle.success,
            disabled=not (self.give or self.take or self.pay or self.ask),
            row=3,
        )
        send.callback = self._send
        self.add_item(send)
        cancel = discord.ui.Button(label="Cancelar", row=3)
        cancel.callback = self._cancel
        self.add_item(cancel)

    def embed(self) -> discord.Embed:
        values = market_values(self.give | self.take, self.database_path)
        by_id = {int(player["id"]): player for player in self.mine + self.theirs}

        def side(ids: set[int], amount: int) -> tuple[str, int]:
            lines = [player_line(by_id[i], values.get(i)) for i in ids if i in by_id]
            if amount:
                lines.append(f"{coins(amount)} Memilets")
            total = sum(values.get(i, 0) for i in ids) + amount
            return "\n".join(lines) or "—", total

        give_text, give_total = side(self.give, self.pay)
        take_text, take_total = side(self.take, self.ask)
        embed = discord.Embed(
            title=f"🤝 Troca com {self.target.display_name}",
            description=(
                "Escolha nos menus o que você dá e o que pede (até "
                f"{MAX_TRADE_PLAYERS} jogadores de cada lado). Um lado pode ficar vazio: "
                "só Memilets é uma compra; nada é um pedido de presente."
            ),
            color=discord.Color.gold(),
        )
        embed.add_field(name="📤 Você dá", value=give_text[:1024], inline=True)
        embed.add_field(name="📥 Você recebe", value=take_text[:1024], inline=True)
        if give_total or take_total:
            difference = take_total - give_total
            balance = (
                "equilibrada"
                if abs(difference) <= max(give_total, take_total) * 0.1
                else (
                    f"{coins(difference)} a seu favor"
                    if difference > 0
                    else f"{coins(-difference)} a favor de {self.target.display_name}"
                )
            )
            embed.add_field(
                name="⚖️ Pelo valor de venda",
                value=f"{coins(give_total)} × {coins(take_total)} · {balance}",
                inline=False,
            )
        return embed

    async def _refresh(self, interaction: discord.Interaction) -> None:
        self._build()
        await interaction.response.edit_message(content=None, embed=self.embed(), view=self)

    def _merge(self, current: set[int], players, side: str, values: list[str]) -> set[int]:
        visible = {
            int(option.value) for option in _squad_options(players, current, self.queries[side])
        }
        return (current - visible) | {int(value) for value in values}

    async def _pick_mine(self, interaction: discord.Interaction) -> None:
        self.give = self._merge(self.give, self.mine, "mine", interaction.data.get("values", []))
        await self._refresh(interaction)

    async def _pick_theirs(self, interaction: discord.Interaction) -> None:
        self.take = self._merge(
            self.take, self.theirs, "theirs", interaction.data.get("values", [])
        )
        await self._refresh(interaction)

    def _searcher(self, side: str):
        async def search(interaction: discord.Interaction) -> None:
            async def apply(modal_interaction: discord.Interaction, text: str) -> None:
                self.queries[side] = text
                await self._refresh(modal_interaction)

            title = (
                "Buscar no seu elenco"
                if side == "mine"
                else f"Buscar no elenco de {self.target.display_name}"
            )
            await interaction.response.send_modal(SearchModal(title, apply))

        return search

    async def _money(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(MoneyModal(self))

    async def _send(self, interaction: discord.Interaction) -> None:
        try:
            trade_id = create_trade(
                self.author.id,
                self.author.display_name,
                self.target.id,
                self.target.display_name,
                sorted(self.give),
                sorted(self.take),
                self.pay,
                self.ask,
                self.database_path,
            )
        except TransactionError as error:
            embed = self.embed()
            embed.set_footer(text=f"⚠️ {error}")
            await interaction.response.edit_message(embed=embed, view=self)
            return
        self.stop()
        await interaction.response.edit_message(
            content=f"📨 Proposta #{trade_id} enviada para {self.target.display_name}.",
            embed=None,
            view=None,
        )
        offer = TradeOfferView(trade_id, self.author.id, self.target.id, self.database_path)
        offer.message = await interaction.followup.send(
            content=f"{self.target.mention}, você recebeu uma proposta de troca!",
            embed=offer.embed(),
            view=offer,
            wait=True,
        )

    async def _cancel(self, interaction: discord.Interaction) -> None:
        self.stop()
        await interaction.response.edit_message(content="Troca cancelada.", embed=None, view=None)


class MoneyModal(discord.ui.Modal):
    def __init__(self, builder: TradeBuilderView):
        super().__init__(title="Memilets na troca")
        self.builder = builder
        self.pay = discord.ui.TextInput(
            label="Você paga", default=str(builder.pay), required=False, max_length=9
        )
        self.ask = discord.ui.TextInput(
            label=f"Você pede de {builder.target.display_name}"[:45],
            default=str(builder.ask),
            required=False,
            max_length=9,
        )
        self.add_item(self.pay)
        self.add_item(self.ask)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        def amount(text: str) -> int:
            digits = re.sub(r"[.\s]", "", text or "0")
            return int(digits) if digits.isdigit() else 0

        self.builder.pay = amount(str(self.pay.value))
        self.builder.ask = amount(str(self.ask.value))
        await self.builder._refresh(interaction)


class TradeOfferView(discord.ui.View):
    """A proposta enviada: a pessoa convidada aceita ou recusa; quem propôs pode cancelar."""

    def __init__(self, trade_id: int, proposer_id: int, recipient_id: int, database_path):
        super().__init__(timeout=900)
        self.trade_id = trade_id
        self.proposer_id = proposer_id
        self.recipient_id = recipient_id
        self.database_path = database_path
        self.message: discord.Message | None = None

    def embed(self, status: str | None = None) -> discord.Embed:
        trade = trade_details(self.trade_id, self.database_path) or {}
        status = status or trade.get("status", "pending")

        def side(players, amount) -> str:
            lines = [player_line(player, player["value"]) for player in players]
            if amount:
                lines.append(f"{coins(amount)} Memilets")
            return "\n".join(lines) or "—"

        embed = discord.Embed(
            title=f"🤝 Proposta de troca #{self.trade_id}"
            + (f" · {STATUS_TEXT[status]}" if status in STATUS_TEXT else ""),
            color=(
                discord.Color.green()
                if status == "accepted"
                else discord.Color.gold() if status == "pending" else discord.Color.dark_grey()
            ),
        )
        embed.add_field(
            name="📤 Oferece",
            value=f"<@{self.proposer_id}>\n"
            + side(trade.get("proposer_players", []), trade.get("proposer_memilets", 0)),
            inline=True,
        )
        embed.add_field(
            name="📥 Pede",
            value=f"<@{self.recipient_id}>\n"
            + side(trade.get("recipient_players", []), trade.get("recipient_memilets", 0)),
            inline=True,
        )
        if status == "pending":
            embed.set_footer(
                text="Expira em 15 minutos. Também dá para responder com "
                f"mm!troca aceitar|recusar {self.trade_id}."
            )
        return embed

    async def _finish(self, interaction: discord.Interaction, status: str) -> None:
        self.stop()
        await interaction.response.edit_message(content=None, embed=self.embed(status), view=None)

    async def _answer(self, interaction: discord.Interaction, accept: bool) -> None:
        if interaction.user.id != self.recipient_id:
            await interaction.response.send_message(
                "Só a pessoa convidada pode responder.", ephemeral=True
            )
            return
        try:
            status = settle_trade(self.trade_id, interaction.user.id, accept, self.database_path)
        except TransactionError as error:
            await interaction.response.send_message(str(error), ephemeral=True)
            return
        await self._finish(interaction, status)

    @discord.ui.button(label="Aceitar", emoji="✅", style=discord.ButtonStyle.success)
    async def accept(self, interaction: discord.Interaction, button) -> None:
        await self._answer(interaction, True)

    @discord.ui.button(label="Recusar", emoji="✖️", style=discord.ButtonStyle.secondary)
    async def decline(self, interaction: discord.Interaction, button) -> None:
        await self._answer(interaction, False)

    @discord.ui.button(label="Cancelar proposta", style=discord.ButtonStyle.danger)
    async def cancel(self, interaction: discord.Interaction, button) -> None:
        if interaction.user.id != self.proposer_id:
            await interaction.response.send_message(
                "Só quem fez a proposta pode cancelá-la.", ephemeral=True
            )
            return
        try:
            cancel_trade(self.trade_id, interaction.user.id, self.database_path)
        except TransactionError as error:
            await interaction.response.send_message(str(error), ephemeral=True)
            return
        await self._finish(interaction, "cancelled")

    async def on_timeout(self) -> None:
        if self.message:
            try:
                await self.message.edit(embed=self.embed("expired"), view=None)
            except discord.HTTPException:
                pass


# --- Cog --------------------------------------------------------------------------------


def _split_amounts(text: str) -> tuple[str, list[int]]:
    """Separa os Memilets do fim do texto (`... por X 100 50`)."""
    found = re.search(r"((?:\s+\d+){1,2})\s*$", text)
    if not found:
        return text, []
    return text[: found.start()].strip(), [int(value) for value in found.group(1).split()]


class TradesCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.database_path = getattr(bot, "database_path", DATABASE_PATH)

    def _owned(self, user_id: int) -> list[dict[str, Any]]:
        return list_owned_players(user_id, self.database_path, limit=5000, sort_by="overall")

    async def _resolve(self, context, query: str, players, on_chosen) -> None:
        """Acha o jogador pelo nome; com homônimos, abre um menu para escolher."""
        player, candidates = resolve_player(query, players)
        if player is not None:
            await on_chosen(lambda **kwargs: context.reply(mention_author=False, **kwargs), player)
            return
        if not candidates:
            await context.reply(f"Não achei **{query[:60]}** no elenco.", mention_author=False)
            return

        async def chosen(interaction: discord.Interaction, picked: dict) -> None:
            await on_chosen(interaction.response.edit_message, picked)

        await context.reply(
            ambiguity_text(query, candidates),
            view=PlayerChoiceView(context.author.id, candidates, chosen),
            mention_author=False,
        )

    # Venda

    @commands.command(name="vender", aliases=["demitir", "vende", "sell"])
    async def sell(self, context: commands.Context, *, query: str | None = None) -> None:
        register_user(context.author.id, context.author.display_name, self.database_path)
        quick = self._quick_filter(query or "")
        if quick is not None:
            await self._quick_sale(context, query, quick)
            return
        if not query:
            view = SalePanelView(context.author.id, self.database_path)
            view.message = await context.reply(embed=view.embed(), view=view, mention_author=False)
            return

        async def confirm(send, player: dict) -> None:
            try:
                quote = sale_quote(context.author.id, [player["id"]], self.database_path)
            except TransactionError as error:
                await send(content=str(error), embed=None, view=None)
                return

            async def done(interaction: discord.Interaction) -> None:
                await _finish_sale(
                    interaction, context.author.id, [player["id"]], self.database_path
                )

            view = ConfirmView(
                context.author.id,
                done,
                confirm_label=f"Vender por {quote['total']:,}".replace(",", "."),
                cancel_text="Venda cancelada.",
                danger=True,
            )
            view.message = await send(content=None, embed=sale_embed(quote), view=view)

        await self._resolve(context, query, self._owned(context.author.id), confirm)

    def _quick_filter(self, text: str) -> dict[str, Any] | None:
        """`base`, `abaixo 70` ou `posicao ZAG`; None se o texto é nome de jogador."""
        words = normalize_player_name(text).split()
        if not words:
            return None
        tiers = {normalize_player_name(tier[0]): tier[0] for tier in RARITY_TIERS}
        if len(words) == 1 and words[0] in tiers:
            return {"rarity": tiers[words[0]]}
        if len(words) == 2 and words[0] in {"abaixo", "menor", "menores"} and words[1].isdigit():
            return {"below": int(words[1])}
        if len(words) == 2 and words[0] in {"posicao", "pos"}:
            return {"position": words[1].upper()}
        return None

    async def _quick_sale(self, context, query: str, filters: dict[str, Any]) -> None:
        ids = quick_sale_ids(context.author.id, self.database_path, **filters)
        if not ids:
            await context.reply(
                f"Nenhum reserva para vender com **{query}** (titulares e 🔒 ficam de fora).",
                mention_author=False,
            )
            return
        quote = sale_quote(context.author.id, ids, self.database_path)

        async def done(interaction: discord.Interaction) -> None:
            await _finish_sale(interaction, context.author.id, ids, self.database_path)

        embed = sale_embed(quote)
        embed.set_footer(text="Venda rápida: só reservas sem 🔒. A venda não pode ser desfeita.")
        view = ConfirmView(
            context.author.id,
            done,
            confirm_label=f"Vender {len(ids)} por {quote['total']:,}".replace(",", "."),
            cancel_text="Venda cancelada.",
            danger=True,
        )
        view.message = await context.reply(embed=embed, view=view, mention_author=False)

    @commands.command(name="proteger", aliases=["cadeado", "lock", "favorito"])
    async def protect(self, context: commands.Context, *, query: str | None = None) -> None:
        register_user(context.author.id, context.author.display_name, self.database_path)
        owned = self._owned(context.author.id)
        if not query:
            # Sem nome: o painel de venda já aberto nos protegidos (tudo por botão).
            view = SalePanelView(context.author.id, self.database_path)
            view.mode = "protected"
            view._build()
            view.message = await context.reply(embed=view.embed(), view=view, mention_author=False)
            return

        async def toggle(send, player: dict) -> None:
            locked = toggle_protection(context.author.id, int(player["id"]), self.database_path)
            await send(
                content=(
                    f"🔒 **{player_label(player)}** protegido: não aparece mais em vendas."
                    if locked
                    else f"🔓 **{player_label(player)}** sem proteção."
                ),
                embed=None,
                view=None,
            )

        await self._resolve(context, query, owned, toggle)

    # Doação

    @commands.command(name="dar", aliases=["doar", "presentear"])
    async def give(self, context: commands.Context, *, arguments: str = "") -> None:
        target = next((member for member in context.message.mentions if not member.bot), None)
        if target is None or target.id == context.author.id:
            await context.reply(
                "Use `mm!dar @pessoa <jogador>` para doar um jogador ou "
                "`mm!dar @pessoa <valor>` para doar Memilets.",
                mention_author=False,
            )
            return
        text = re.sub(r"<@!?\d+>", " ", arguments).strip()
        amount = re.sub(r"[.\s]", "", text)
        if amount.isdigit():
            await self._confirm_gift(
                context,
                target,
                [],
                int(amount),
                lambda **kwargs: context.reply(mention_author=False, **kwargs),
            )
            return
        if not text:
            await context.reply(
                f"O que você quer dar para {target.display_name}? "
                "`mm!dar @pessoa <jogador>` ou `mm!dar @pessoa <valor>`.",
                mention_author=False,
            )
            return

        async def chosen(send, player: dict) -> None:
            await self._confirm_gift(context, target, [player], 0, send)

        await self._resolve(context, text, self._owned(context.author.id), chosen)

    async def _confirm_gift(self, context, target, players, amount: int, send) -> None:
        lines = []
        if players:
            with connect(self.database_path) as connection:
                lineup = {
                    int(row["player_id"])
                    for row in connection.execute(
                        "SELECT player_id FROM team_lineups WHERE discord_user_id = ?",
                        (str(context.author.id),),
                    )
                }
            for player in players:
                lines.append(player_line(player))
                details = dict(player, starter=int(player["id"]) in lineup)
                lines.extend(
                    f"└ ⚠️ {warning}" for warning in player_warnings(details, selling=False)
                )
        if amount:
            lines.append(f"{coins(amount)} Memilets")
        embed = discord.Embed(
            title=f"🎁 Doar para {target.display_name}?",
            description="\n".join(lines),
            color=discord.Color.teal(),
        )
        embed.set_footer(text="A doação vai direto, sem precisar que a outra pessoa aceite.")

        async def done(interaction: discord.Interaction) -> None:
            try:
                given = give_players(
                    context.author.id,
                    context.author.display_name,
                    target.id,
                    target.display_name,
                    [player["id"] for player in players],
                    amount,
                    self.database_path,
                )
            except TransactionError as error:
                await interaction.response.edit_message(content=str(error), embed=None, view=None)
                return
            gifts = [f"**{player_label(player)}**" for player in given]
            if amount:
                gifts.append(f"{coins(amount)} Memilets")
            await interaction.response.edit_message(
                content=(
                    f"🎁 {target.mention} recebeu {' e '.join(gifts)} "
                    f"de {context.author.mention}!"
                ),
                embed=None,
                view=None,
            )

        view = ConfirmView(
            context.author.id, done, confirm_label="Doar", cancel_text="Doação cancelada."
        )
        view.message = await send(content=None, embed=embed, view=view)

    # Troca

    @commands.command(name="troca", aliases=["trocar", "trade"])
    async def trade(self, context: commands.Context, *, arguments: str = "") -> None:
        parts = arguments.split()
        if len(parts) == 2 and parts[0].casefold() in {"aceitar", "recusar", "cancelar"}:
            await self._answer_by_text(context, parts[0].casefold(), parts[1])
            return
        target = next((member for member in context.message.mentions if not member.bot), None)
        if target is None:
            await context.reply(
                "Use `mm!troca @pessoa` para montar a troca nos menus.\n"
                "Atalho: `mm!troca @pessoa <seus jogadores> por <jogadores da pessoa> "
                "[seus Memilets] [Memilets da pessoa]` (separe vários com vírgula).",
                mention_author=False,
            )
            return
        if target.id == context.author.id:
            await context.reply("Você não pode trocar com você mesmo.", mention_author=False)
            return
        register_user(target.id, target.display_name, self.database_path)
        builder = TradeBuilderView(context.author, target, self.database_path)
        text = re.sub(r"<@!?\d+>", " ", arguments).strip()
        if text:
            problem = self._prefill(builder, text)
            if problem:
                await context.reply(problem, mention_author=False)
                return
        builder.message = await context.reply(
            embed=builder.embed(), view=builder, mention_author=False
        )

    def _prefill(self, builder: TradeBuilderView, text: str) -> str | None:
        """Preenche o montador a partir do atalho em texto; devolve o erro, se houver."""
        halves = re.split(r"\s+por\s+", f" {text} ", maxsplit=1, flags=re.IGNORECASE)
        if len(halves) != 2:
            return "No atalho, separe o que você dá e o que pede com `por`."
        mine_text = halves[0].strip()
        theirs_text, amounts = _split_amounts(halves[1].strip())
        picked: dict[str, list[int]] = {"mine": [], "theirs": []}
        for side, chunk, players, owner in (
            ("mine", mine_text, builder.mine, "no seu elenco"),
            ("theirs", theirs_text, builder.theirs, f"no elenco de {builder.target.display_name}"),
        ):
            for name in filter(None, (part.strip() for part in chunk.split(","))):
                player, _ = resolve_player(name, players)
                if player is None:
                    return f"Não identifiquei **{name[:60]}** {owner}. Use um nome mais completo."
                picked[side].append(int(player["id"]))
        pay = amounts[0] if amounts else 0
        ask = amounts[1] if len(amounts) > 1 else 0
        builder.prefill(picked["mine"], picked["theirs"], pay, ask)
        return None

    async def _answer_by_text(self, context, action: str, raw_id: str) -> None:
        if not raw_id.lstrip("#").isdigit():
            await context.reply("Informe o número da proposta.", mention_author=False)
            return
        trade_id = int(raw_id.lstrip("#"))
        try:
            if action == "cancelar":
                cancel_trade(trade_id, context.author.id, self.database_path)
                status = "cancelled"
            else:
                status = settle_trade(
                    trade_id, context.author.id, action == "aceitar", self.database_path
                )
        except TransactionError as error:
            await context.reply(str(error), mention_author=False)
            return
        await context.reply(f"{STATUS_TEXT[status]} (#{trade_id}).", mention_author=False)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(TradesCog(bot))
