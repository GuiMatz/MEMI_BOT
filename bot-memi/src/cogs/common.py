"""Peças de interface usadas por vários comandos do FUT.AE."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import discord

from src.accents import rarity_color
from src.card_image import animated_photo, render_player_card, render_player_card_gif
from src.ratings import rarity_label


def player_label(player: dict[str, Any]) -> str:
    return str(player.get("display_name") or player.get("name") or "—")


def card_filename(player: dict[str, Any]) -> str:
    """`carta.gif` quando a foto principal é um GIF animado; senão `carta.png`."""
    return "carta.gif" if animated_photo(player) else "carta.png"


async def card_file(player: dict[str, Any]) -> discord.File:
    if card_filename(player) == "carta.gif":
        animated = await asyncio.to_thread(render_player_card_gif, player)
        if animated is not None:
            return discord.File(animated, filename="carta.gif")
    image = await asyncio.to_thread(render_player_card, player)
    return discord.File(image, filename="carta.png")


def card_embed(
    player: dict[str, Any],
    *,
    title_prefix: str = "",
    footer: str = "",
    color: discord.Color | None = None,
) -> discord.Embed:
    """Ficha do jogador com a carta vertical como imagem principal."""
    overall = player.get("effective_overall") or player.get("overall")
    owner = player.get("owner_name")
    lines = [
        f"**{overall or '—'}** · {player.get('game_position') or '—'} · {rarity_label(overall)}",
        f"🏟️ {player.get('club_name') or 'Passe livre'}",
    ]
    slot = player.get("lineup_position")
    if slot:
        natural = player.get("game_position")
        lines.insert(1, "✅ Titular" + (f" como {slot}" if natural and slot != natural else ""))
    if player.get("balls"):
        lines.append(f"⚽ {player['balls']}/10 bolas")
    if player.get("locked"):
        lines.append("🔒 Protegido (não aparece em vendas)")
    if player.get("pack_origin"):
        lines.append("📦 Veio de pacote: vende por 25% do valor")
    if "owner_name" in player:
        lines.append(f"🔒 Contratado por **{owner}**" if owner else "🟢 Livre para contratar")
    embed = discord.Embed(
        title=f"{title_prefix}{player_label(player)}",
        description="\n".join(lines),
        color=color or rarity_color(overall),
    )
    embed.set_image(url=f"attachment://{card_filename(player)}")
    if footer:
        embed.set_footer(text=footer)
    return embed


# Ordenação: chave → (rótulo do menu, função de ordenação, decrescente?).
SortOption = tuple[str, Callable[[dict[str, Any]], Any], bool]


class CardPagerView(discord.ui.View):
    """Uma carta por página, como o `$mm` do Mudae; ◀ ▶ passam para o próximo jogador.

    `line_format(posição, jogador)` gera as linhas do botão Lista; `sorts` acrescenta um
    menu "Ordenar por" que reordena as cartas e a lista (a primeira opção é a inicial).
    """

    def __init__(
        self,
        players: list[dict[str, Any]],
        *,
        title: str = "",
        list_lines=None,
        line_format: Callable[[int, dict[str, Any]], str] | None = None,
        sorts: dict[str, SortOption] | None = None,
    ):
        super().__init__(timeout=300)
        self.players = players
        self.title = title
        self.index = 0
        self.list_lines = list_lines
        self.line_format = line_format
        self.sorts = sorts or {}
        self.sort_key = next(iter(self.sorts), None)
        self.message: discord.Message | None = None
        if self.sort_key:
            self._apply_sort()
            self.sort_menu = discord.ui.Select(placeholder="Ordenar por…", row=1)
            self.sort_menu.callback = self._sorted
            self.add_item(self.sort_menu)
        self._sync()

    def lines(self) -> list[str]:
        if self.line_format is not None:
            return [
                self.line_format(index, player)
                for index, player in enumerate(self.players, start=1)
            ]
        return self.list_lines or []

    def _apply_sort(self) -> None:
        _, key, reverse = self.sorts[self.sort_key]
        self.players = sorted(self.players, key=key, reverse=reverse)

    def _sync(self) -> None:
        self.previous.disabled = self.index == 0
        self.next.disabled = self.index >= len(self.players) - 1
        if self.list_lines is None and self.line_format is None:
            self.remove_item(self.show_list)
        if self.sort_key:
            self.sort_menu.options = [
                discord.SelectOption(label=label, value=key, default=key == self.sort_key)
                for key, (label, _, _) in self.sorts.items()
            ]

    async def _sorted(self, interaction: discord.Interaction) -> None:
        self.sort_key = interaction.data["values"][0]
        self._apply_sort()
        self.index = 0
        await self._show(interaction)

    def page(self) -> discord.Embed:
        player = self.players[self.index]
        order = f" · {self.sorts[self.sort_key][0]}" if self.sort_key else ""
        return card_embed(
            player,
            footer=f"{self.title} · {self.index + 1}/{len(self.players)}{order}".strip(" ·"),
        )

    async def send(self, send: Callable[..., Awaitable[discord.Message]]) -> None:
        file = await card_file(self.players[self.index])
        self.message = await send(embed=self.page(), file=file, view=self)

    async def _show(self, interaction: discord.Interaction) -> None:
        self._sync()
        await interaction.response.defer()
        file = await card_file(self.players[self.index])
        await interaction.edit_original_response(embed=self.page(), attachments=[file], view=self)

    @discord.ui.button(label="◀", style=discord.ButtonStyle.secondary)
    async def previous(self, interaction: discord.Interaction, button) -> None:
        self.index -= 1
        await self._show(interaction)

    @discord.ui.button(label="▶", style=discord.ButtonStyle.secondary)
    async def next(self, interaction: discord.Interaction, button) -> None:
        self.index += 1
        await self._show(interaction)

    @discord.ui.button(label="Lista", emoji="📋", style=discord.ButtonStyle.primary)
    async def show_list(self, interaction: discord.Interaction, button) -> None:
        lines = self.lines()
        embed = discord.Embed(
            title=self.title + (f" · {self.sorts[self.sort_key][0]}" if self.sort_key else ""),
            description="\n".join(lines[:40])[:4000]
            + (f"\n… e mais {len(lines) - 40}" if len(lines) > 40 else ""),
            color=discord.Color.green(),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


class PlayerChoiceView(discord.ui.View):
    """Menu para escolher entre homônimos; chama `on_choose(interaction, jogador)`."""

    def __init__(
        self,
        author_id: int,
        players: list[dict[str, Any]],
        on_choose: Callable[[discord.Interaction, dict[str, Any]], Awaitable[None]],
        *,
        placeholder: str = "Qual deles?",
    ):
        super().__init__(timeout=120)
        self.author_id = author_id
        self.players = players[:25]
        self.on_choose = on_choose
        select = discord.ui.Select(
            placeholder=placeholder,
            options=[
                discord.SelectOption(
                    label=f"{player.get('overall') or '—'} · {player_label(player)}"[:100],
                    description=(
                        f"{player.get('game_position') or '—'} · "
                        f"{player.get('club_name') or 'Passe livre'} · ID {player['id']}"
                    )[:100],
                    value=str(index),
                )
                for index, player in enumerate(self.players)
            ],
        )
        select.callback = self._chosen
        self.add_item(select)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("Esse menu não é seu.", ephemeral=True)
            return False
        return True

    async def _chosen(self, interaction: discord.Interaction) -> None:
        self.stop()
        await self.on_choose(interaction, self.players[int(interaction.data["values"][0])])


class ConfirmView(discord.ui.View):
    """Botões Confirmar/Cancelar que só quem pediu pode usar.

    `on_confirm(interaction)` faz a ação e responde à interação; o cancelamento troca a
    mensagem por `cancel_text`.
    """

    def __init__(
        self,
        author_id: int,
        on_confirm: Callable[[discord.Interaction], Awaitable[None]],
        *,
        confirm_label: str = "Confirmar",
        cancel_text: str = "Cancelado.",
        danger: bool = False,
        timeout: float = 120,
    ):
        super().__init__(timeout=timeout)
        self.author_id = author_id
        self.on_confirm = on_confirm
        self.cancel_text = cancel_text
        self.message: discord.Message | None = None
        self.confirm.label = confirm_label
        if danger:
            self.confirm.style = discord.ButtonStyle.danger

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "Só quem fez o pedido pode confirmar.", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="Confirmar", style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, button) -> None:
        self.stop()
        await self.on_confirm(interaction)

    @discord.ui.button(label="Cancelar", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button) -> None:
        self.stop()
        await interaction.response.edit_message(content=self.cancel_text, embed=None, view=None)

    async def on_timeout(self) -> None:
        if self.message:
            try:
                await self.message.edit(view=None)
            except discord.HTTPException:
                pass


def intro_embed(title: str, intro: str, *, color: discord.Color | None = None) -> discord.Embed:
    """Embed de menu com o parágrafo que explica o sistema logo no topo."""
    return discord.Embed(
        title=title, description=f"*{intro}*", color=color or discord.Color.blurple()
    )


def ambiguity_text(query: str, players: list[dict[str, Any]]) -> str:
    if not players:
        return f"Não encontrei nenhum jogador chamado **{query[:60]}**."
    return (
        f"Encontrei **{len(players)}** jogadores para **{query[:60]}**. "
        "Escolha no menu abaixo (ou use o ID que aparece nele)."
    )
