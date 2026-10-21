"""Peças de interface usadas por vários comandos do FUT.AE."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import discord

from src.card_image import render_player_card
from src.ratings import rarity_label


def player_label(player: dict[str, Any]) -> str:
    return str(player.get("display_name") or player.get("name") or "—")


async def card_file(player: dict[str, Any]) -> discord.File:
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
    if player.get("balls"):
        lines.append(f"⚽ {player['balls']}/10 bolas")
    if "owner_name" in player:
        lines.append(f"🔒 Contratado por **{owner}**" if owner else "🟢 Livre para contratar")
    embed = discord.Embed(
        title=f"{title_prefix}{player_label(player)}",
        description="\n".join(lines),
        color=color or (discord.Color.gold() if (overall or 0) >= 90 else discord.Color.blurple()),
    )
    embed.set_image(url="attachment://carta.png")
    if footer:
        embed.set_footer(text=footer)
    return embed


class CardPagerView(discord.ui.View):
    """Uma carta por página, como o `$mm` do Mudae; ◀ ▶ passam para o próximo jogador."""

    def __init__(self, players: list[dict[str, Any]], *, title: str = "", list_lines=None):
        super().__init__(timeout=300)
        self.players = players
        self.title = title
        self.index = 0
        self.list_lines = list_lines
        self.message: discord.Message | None = None
        self._sync()

    def _sync(self) -> None:
        self.previous.disabled = self.index == 0
        self.next.disabled = self.index >= len(self.players) - 1
        if self.list_lines is None:
            self.remove_item(self.show_list)

    def page(self) -> discord.Embed:
        player = self.players[self.index]
        return card_embed(
            player,
            footer=f"{self.title} · {self.index + 1}/{len(self.players)}".strip(" ·"),
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
        lines = self.list_lines or []
        embed = discord.Embed(
            title=self.title,
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


def ambiguity_text(query: str, players: list[dict[str, Any]]) -> str:
    if not players:
        return f"Não encontrei nenhum jogador chamado **{query[:60]}**."
    return (
        f"Encontrei **{len(players)}** jogadores para **{query[:60]}**. "
        "Escolha no menu abaixo (ou use o ID que aparece nele)."
    )
