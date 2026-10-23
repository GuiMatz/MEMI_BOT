"""Comandos iniciais de consulta e conta do jogo."""

from __future__ import annotations

import re
from datetime import datetime

import discord
from discord.ext import commands

from src import icons
from src.cosmetics import COSMETIC_KINDS, LABEL_TO_KIND, free_counts, owners_of
from src.db import DATABASE_PATH, normalize_player_name
from src.command_help import command_help_embed
from src.economy import EconomyError, activate_rolls, get_account_status
from src.cogs.common import (
    CardPagerView,
    PlayerChoiceView,
    ambiguity_text,
    card_embed,
    card_file,
    player_choices,
    player_label,
)
from src.game import (
    find_player,
    get_catalog_entries,
    get_collection_left,
    list_owned_players,
    register_user,
    search_catalog,
    search_players,
    top_player_cards,
)
from src.player_images import (
    ImageError,
    add_image,
    delete_image,
    list_images,
    missing_photos,
    set_main,
)
from src.roll_images import find_image, image_file
from src.teams import get_lineup

PAGE_SIZE = 20
POSITION_ORDER = {
    position: index
    for index, position in enumerate(("GOL", "ZAG", "LD", "LE", "MC", "MEI", "PE", "PD", "CA"))
}


def _overall(player: dict) -> int:
    return int(player.get("overall") or 0)


def _name_key(player: dict) -> str:
    return normalize_player_name(str(player.get("name") or ""))


# Ordem do `mm!elenco` (a primeira é a inicial).
ROSTER_SORTS = {
    "overall": ("Overall: maior → menor", lambda p: (-_overall(p), _name_key(p)), False),
    "overall_asc": ("Overall: menor → maior", lambda p: (_overall(p), _name_key(p)), False),
    "titulares": (
        "Titulares primeiro (na ordem do campo)",
        lambda p: (p.get("slot_order") is None, p.get("slot_order") or 0, -_overall(p)),
        False,
    ),
    "posicao": (
        "Posição (GOL → CA)",
        lambda p: (POSITION_ORDER.get(p.get("game_position"), 99), -_overall(p)),
        False,
    ),
    "nome": ("Nome (A → Z)", _name_key, False),
    "recentes": ("Contratados mais recentes", lambda p: str(p.get("acquired_at") or ""), True),
    "antigos": ("Contratados mais antigos", lambda p: str(p.get("acquired_at") or ""), False),
}


def roster_line(index: int, player: dict) -> str:
    """`  1. ⭐ 88 — Nome (PE›PD) 🔒📦`: titulares, vaga, protegido e veio de pacote."""
    natural = player.get("game_position") or "posição pendente"
    slot = player.get("lineup_position")
    position = natural if not slot or slot == natural else f"{natural}›{slot}"
    mark = "⭐" if slot else "▫️"
    tags = ("🔒" if player.get("locked") else "") + ("📦" if player.get("pack_origin") else "")
    return (
        f"`{index:>3}.` {mark} **{player.get('overall') or '—'}** — {player['name']} "
        f"({position}){' ' + tags if tags else ''}"
    )


TOP_CATEGORIES = "jogadores, cartola, tecnicos, estadios, ligas, emblemas, uniformes ou mascotes"


def _thousands(value: int) -> str:
    return f"{value:,}".replace(",", ".")


def _timestamp(moment: datetime | None, ready: str = "**disponível agora**") -> str:
    """Horário relativo do Discord (ex.: "em 12 minutos") ou o texto de pronto."""
    if moment is None:
        return ready
    unix = int(moment.timestamp())
    return f"<t:{unix}:R> (<t:{unix}:t>)"


async def player_embed(player: dict) -> tuple[discord.Embed, discord.File]:
    """Ficha do jogador com a carta vertical (foto grande) e o dono, se houver."""
    return card_embed(player, footer="mm!wish <nome> para desejar"), await card_file(player)


def catalog_embed(
    item: dict, database_path=DATABASE_PATH
) -> tuple[discord.Embed, discord.File | None]:
    """Ficha de técnico, estádio, emblema, liga, uniforme ou mascote, com imagem."""
    description = f"**{item['kind']}**"
    kind = LABEL_TO_KIND.get(item["kind"])
    if kind:
        owners = owners_of(kind, item["name"], database_path)
        description += (
            "\n🔒 Na coleção de " + ", ".join(f"**{owner}**" for owner in owners[:10])
            if owners
            else "\n🟢 Ninguém tem ainda · sai nas crates (`mm!crates`)"
        )
    embed = discord.Embed(
        title=item["name"],
        description=description,
        color=discord.Color.teal(),
    )
    path = find_image(item.get("image_path"), item.get("entity"), item.get("id"))
    if path is None:
        embed.set_footer(text="Sem imagem local para este item.")
        return embed, None
    file = image_file(path, "item")
    embed.set_image(url=f"attachment://{file.filename}")
    return embed, file


class PagedView(discord.ui.View):
    """Lista paginada genérica com botões ◀ ▶."""

    def __init__(self, title: str, lines: list[str], *, color=None, footer: str = ""):
        super().__init__(timeout=300)
        self.title = title
        self.pages = [lines[index : index + PAGE_SIZE] for index in range(0, len(lines), PAGE_SIZE)]
        self.pages = self.pages or [[]]
        self.color = color or discord.Color.gold()
        self.footer = footer
        self.index = 0
        self.message: discord.Message | None = None
        self._sync_buttons()

    def _sync_buttons(self) -> None:
        self.previous.disabled = self.index == 0
        self.next.disabled = self.index >= len(self.pages) - 1

    def embed(self) -> discord.Embed:
        embed = discord.Embed(
            title=self.title,
            description="\n".join(self.pages[self.index]) or "Nenhum item encontrado.",
            color=self.color,
        )
        footer = f"Página {self.index + 1}/{len(self.pages)}"
        embed.set_footer(text=f"{footer} · {self.footer}" if self.footer else footer)
        return embed

    async def _refresh(self, interaction: discord.Interaction) -> None:
        self._sync_buttons()
        await interaction.response.edit_message(embed=self.embed(), view=self)

    @discord.ui.button(label="◀", style=discord.ButtonStyle.secondary)
    async def previous(self, interaction: discord.Interaction, button) -> None:
        self.index -= 1
        await self._refresh(interaction)

    @discord.ui.button(label="▶", style=discord.ButtonStyle.secondary)
    async def next(self, interaction: discord.Interaction, button) -> None:
        self.index += 1
        await self._refresh(interaction)

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


class ScoutSelect(discord.ui.Select):
    def __init__(self, results: list[dict], database_path=DATABASE_PATH):
        self.results = results
        self.database_path = database_path
        options = []
        for index, result in enumerate(results[:25]):
            if result["type"] == "player":
                player = result["data"]
                label = (player.get("display_name") or player["name"])[:100]
                description = (
                    f"{player['overall'] or '—'} · {player['game_position'] or '—'} · "
                    f"{player['club_name'] or 'Passe livre'}"
                )
                emoji = "⚽"
            else:
                item = result["data"]
                label = item["name"][:100]
                description = item["kind"]
                emoji = "🔎"
            options.append(
                discord.SelectOption(
                    label=label, description=description[:100], value=str(index), emoji=emoji
                )
            )
        super().__init__(placeholder="Escolha um resultado para ver a ficha", options=options)

    async def callback(self, interaction: discord.Interaction) -> None:
        result = self.results[int(self.values[0])]
        if result["type"] == "player":
            embed, file = await player_embed(result["data"])
        else:
            embed, file = catalog_embed(result["data"], self.database_path)
        await interaction.response.edit_message(
            content=None,
            embed=embed,
            attachments=[file] if file else [],
            view=self.view,
        )


class ScoutView(discord.ui.View):
    def __init__(self, results: list[dict], database_path=DATABASE_PATH):
        super().__init__(timeout=180)
        self.add_item(ScoutSelect(results, database_path))


FUTAE_SECTIONS = (
    (
        "🎲 Coleção",
        "`mm!roll` — 10 rolls a cada 30 min (hora cheia e meia hora, Brasília)\n"
        "`mm!tu` — seus timers: rolls, claim (30 min) e daily\n"
        "`mm!scout <nome>` — ficha de jogador, técnico, estádio, emblema, uniforme…\n"
        "`mm!top [categoria]` — catálogo por overall ou nome\n"
        "`mm!left` — jogadores e cosméticos ainda livres\n"
        "`mm!stats [@pessoa]` — vitórias, gols e artilheiros, por campeonato ou amistoso\n"
        "`mm!topi` — o top dos jogadores em cartas, passando para o lado\n"
        "`mm!img <jogador>` — galeria de imagens (📷 Adicionar, ⭐ principal)\n"
        "`mm!semfoto [meus]` — jogadores sem foto, um por vez, para preencher em sequência\n"
        "`mm!elenco [@pessoa] [titulares|reservas|filtro]` — cartas com ⭐ nos titulares "
        "e menu para ordenar\n"
        "`mm!wish`, `mm!wishlist`/`mm!wl`, `mm!starwish`/`mm!sw` — wishes",
    ),
    (
        "🪙 Economia",
        "`mm!daily` — de 400 a 1.200 Memilets (+250 por cosmético equipado)\n"
        "`mm!carteira` — saldo, limites e timers\n"
        "`mm!shop`/`mm!loja` — tudo o que dá para comprar, com botões\n"
        "`mm!upgrade` — upgrades permanentes (sempre pede confirmação)\n"
        "`mm!crates [epica] [1|5|10]` — caixas com rolls guardados, cosméticos e bônus\n"
        "`mm!ativar <quantidade>` — ativa os rolls guardados (+1 contratação a cada 10)\n"
        "`mm!inventario` — caixas e pacotes ganhos nas caixas, para abrir de graça\n"
        "`mm!pacote [epico]` — uma carta de jogador livre do mercado (nunca Base)\n"
        "`mm!allin` — aposta de 75 mil: roleta com 1 em 10 de Ícone ou melhor\n"
        "`mm!construir [1-12]` / `mm!destruir` — Centro de Treinamento (torre)\n"
        "`mm!renda` · `mm!bilheteria` — dinheiro do estádio e do campeonato\n"
        "`mm!missoes` — missões diárias e semanais · `mm!rt` — zera o claim (upgrade)",
    ),
    (
        "🎽 Clube",
        "`mm!cosmeticos [tipo]` — seus emblemas, uniformes, estádios, técnicos…\n"
        "`mm!emblema` · `mm!uniforme` · `mm!estadio` · `mm!tecnico` · `mm!liga` · "
        "`mm!mascote` `<nome>` — equipa o que é seu",
    ),
    (
        "⚽ Time e partidas",
        "`mm!time` — perfil e imagem do time · `mm!personalizar` — nome, cor, escudo e "
        "cosméticos num painel\n"
        "`mm!escalar` — monta o time clicando em cada posição\n"
        "`mm!tatica` — painel de formação, estilo, capitão e cobradores\n"
        "`mm!vender` — painel de venda com filtros, seleção em massa e 🔒 (ou `mm!vender <nome>`)\n"
        "`mm!proteger <nome>` — 🔒 tira o jogador de todas as vendas\n"
        "`mm!troca @pessoa` — monta a troca nos menus · `mm!dar @pessoa <jogador|valor>`\n"
        "`mm!pay @pessoa <quantia>` — manda Memilets para outra pessoa\n"
        "`mm!cassino [valor]` — cara ou coroa, caça-níquel, roleta e minas\n"
        "`mm!amistoso @pessoa [idaevolta] [aposta N]` — partida narrada, ida e volta, apostada\n"
        "`mm!penaltis @pessoa [aposta]` — disputa de pênaltis escolhendo o canto\n"
        "`mm!desafio` — partidas contra times lendários: escada, chefões e desafio livre\n"
        "`mm!campeonato` — campeonato diário; o campeão leva 500 Memilets\n"
        "`mm!temporada [mês]` — tabela do mês · `mm!temporadas` — campeão de cada mês",
    ),
)

GENERAL_HELP_PAGES = (
    (
        # Mesmos comandos da página 1 do mm!ajuda do memi_bot.py (sem os de admin).
        "**🏆 Rankings**\n"
        "`mm!tagarelas` — ranking de mensagens\n"
        "`mm!levels` / `mm!lvl` — níveis, patentes e XP\n"
        "`mm!hall` — vencedores dos meses anteriores\n\n"
        "**🎵 Música**\n"
        "`mm!musicas` — rankings de músicas, artistas e gêneros\n"
        "`mm!aleatoria` — sorteia uma música\n"
        "`mm!wrapped` — seu resumo dos últimos 12 meses\n\n"
        "**🎎 Mudae**\n"
        "`mm!mudae` — panorama do servidor\n"
        "`mm!mudae ios` · `personagens` · `series` — rankings do Mudae\n\n"
        "**👤 Perfil e personalização**\n"
        "`mm!perfil` · `mm!cartao` · `mm!tags` — perfil, cartão e tags\n"
        "`mm!favorita` · `mm!titulo` · `mm!frase` · `mm!ec` — edita seu perfil\n\n"
        "**ℹ️ Sobre o bot**\n"
        "`mm!changelog` — o que mudou em cada versão"
    ),
    (
        "**FUT.AE — comandos do jogo**\n"
        "Use `mm!fut.ae` para abrir diretamente esta página.\n\n"
        + "\n\n".join(f"{name}\n{value}" for name, value in FUTAE_SECTIONS)
    ),
)


class PhotoUploadModal(discord.ui.Modal):
    """Formulário com a imagem anexada; `on_upload(interaction, bytes)` faz o resto."""

    def __init__(self, player_name: str, on_upload):
        super().__init__(title=f"Foto de {player_name}"[:45])
        self.on_upload = on_upload
        self.upload = discord.ui.FileUpload(max_values=1)
        self.add_item(
            discord.ui.Label(
                text="Imagem ou GIF",
                description="Ela é cortada no formato da carta. Rosto no alto fica melhor.",
                component=self.upload,
            )
        )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        files = list(self.upload.values or [])
        if not files:
            await interaction.response.send_message("Nenhuma imagem enviada.", ephemeral=True)
            return
        try:
            data = await files[0].read()
        except discord.HTTPException:
            await interaction.response.send_message("Não consegui baixar a imagem.", ephemeral=True)
            return
        await self.on_upload(interaction, data)


def can_edit_photos(member, player: dict) -> bool:
    """Dono do jogador ou moderação mexem nas fotos (a primeira foto é livre)."""
    moderator = bool(getattr(getattr(member, "guild_permissions", None), "manage_guild", False))
    return moderator or str(player.get("owner_id") or "") == str(member.id)


class MissingPhotosView(discord.ui.View):
    """`mm!semfoto`: um jogador sem foto por vez; enviar a foto já passa para o próximo."""

    def __init__(self, member, database_path, *, mine: bool = False):
        super().__init__(timeout=900)
        self.member = member
        self.database_path = database_path
        self.mine = mine
        self.index = 0
        self.notice = ""
        self.message: discord.Message | None = None
        self._load()

    def _load(self) -> None:
        self.missing, self.total = missing_photos(
            self.database_path, owner_id=self.member.id if self.mine else None
        )
        self.index = min(self.index, max(0, len(self.missing) - 1))
        self._sync()

    def current(self) -> dict | None:
        if not self.missing:
            return None
        found = search_players(str(self.missing[self.index]), self.database_path, limit=1)
        return found[0] if found else None

    def _sync(self) -> None:
        self.previous.disabled = self.index == 0
        self.next.disabled = self.index >= len(self.missing) - 1
        self.upload.disabled = not self.missing
        self.scope.label = "Ver todos" if self.mine else "Só os meus"

    def page(self, player: dict | None) -> discord.Embed:
        done = self.total - len(self.missing)
        share = done / self.total if self.total else 1
        scope = "do seu elenco" if self.mine else "do mercado"
        header = f"📸 **{done}/{self.total}** jogadores {scope} com foto ({share:.0%})"
        if player is None:
            embed = discord.Embed(
                title="📸 Fotos completas!",
                description=f"{header}\nNenhum jogador {scope} está sem foto. 🎉",
                color=discord.Color.green(),
            )
            return embed
        embed = card_embed(
            player,
            title_prefix="📷 Sem foto · ",
            footer=f"{self.index + 1}/{len(self.missing)} sem foto · do maior overall para o menor",
        )
        embed.description = (
            f"{header}\n{embed.description}\n\n"
            "Toque em **📷 Enviar foto** e anexe a imagem: ela já vira a foto da carta e "
            "o painel passa para o próximo." + (f"\n\n{self.notice}" if self.notice else "")
        )
        return embed

    async def send(self, send) -> None:
        player = self.current()
        kwargs = {"embed": self.page(player), "view": self}
        if player is not None:
            kwargs["file"] = await card_file(player)
        self.message = await send(**kwargs)

    async def _show(self, interaction: discord.Interaction) -> None:
        self._sync()
        player = self.current()
        if not interaction.response.is_done():
            await interaction.response.defer()
        attachments = [await card_file(player)] if player is not None else []
        await interaction.edit_original_response(
            embed=self.page(player), attachments=attachments, view=self
        )
        self.notice = ""

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.member.id:
            await interaction.response.send_message(
                "Use `mm!semfoto` para abrir o seu painel.", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="◀", style=discord.ButtonStyle.secondary)
    async def previous(self, interaction: discord.Interaction, button) -> None:
        self.index -= 1
        await self._show(interaction)

    @discord.ui.button(label="Enviar foto", emoji="📷", style=discord.ButtonStyle.success)
    async def upload(self, interaction: discord.Interaction, button) -> None:
        player = self.current()
        if player is None:
            await self._show(interaction)
            return

        async def save(modal_interaction: discord.Interaction, data: bytes) -> None:
            try:
                add_image(int(player["id"]), data, self.member.id, self.database_path)
            except ImageError as error:
                await modal_interaction.response.send_message(str(error), ephemeral=True)
                return
            name = player.get("display_name") or player["name"]
            self.notice = f"✅ Foto de **{name}** salva. Próximo:"
            self._load()  # quem ganhou foto sai da lista; o índice já aponta o próximo
            await self._show(modal_interaction)

        await interaction.response.send_modal(
            PhotoUploadModal(player.get("display_name") or player["name"], save)
        )

    @discord.ui.button(label="▶", style=discord.ButtonStyle.secondary)
    async def next(self, interaction: discord.Interaction, button) -> None:
        self.index += 1
        await self._show(interaction)

    @discord.ui.button(label="Só os meus", style=discord.ButtonStyle.secondary)
    async def scope(self, interaction: discord.Interaction, button) -> None:
        self.mine = not self.mine
        self.index = 0
        self._load()
        await self._show(interaction)

    async def on_timeout(self) -> None:
        if self.message:
            try:
                await self.message.edit(view=None)
            except discord.HTTPException:
                pass


class ImageGalleryView(discord.ui.View):
    """`mm!img`: uma carta por imagem; ⭐ deixa a imagem como principal da carta."""

    def __init__(self, player: dict, viewer_id: int, can_edit: bool, database_path, *, start=None):
        super().__init__(timeout=300)
        self.player = player
        self.viewer_id = viewer_id
        self.can_edit = can_edit
        self.database_path = database_path
        self.notice = ""
        self.message: discord.Message | None = None
        self.images = list_images(int(player["id"]), database_path)
        self.index = 0
        for index, image in enumerate(self.images):
            if (start is None and image["main"]) or image["id"] == start:
                self.index = index
        self._sync()

    def _sync(self) -> None:
        image = self.current()
        self.previous.disabled = self.index == 0
        self.next.disabled = self.index >= len(self.images) - 1
        self.make_main.disabled = not self.can_edit or image is None or image["main"]
        self.delete.disabled = not self.can_edit or image is None or image["source"] == "original"
        self.add.disabled = not (self.can_edit or not self.images)

    def current(self) -> dict | None:
        return self.images[self.index] if self.images else None

    def card(self) -> dict:
        image = self.current()
        return {**self.player, "photo_path": image["image_path"]} if image else self.player

    def page(self) -> discord.Embed:
        image = self.current()
        if image is None:
            footer = "Sem imagens ainda · mm!img <nome> com uma imagem anexada adiciona"
        else:
            tags = [
                "⭐ principal" if image["main"] else "",
                "original" if image["source"] == "original" else "enviada",
            ]
            footer = f"Imagem {self.index + 1}/{len(self.images)} · " + " · ".join(
                t for t in tags if t
            )
        embed = card_embed(self.card(), title_prefix="🖼️ ", footer=footer)
        if self.notice:
            embed.description += f"\n\n{self.notice}"
        if not self.can_edit:
            embed.description += "\n-# Só o dono do jogador ou a moderação muda as imagens."
        return embed

    async def send(self, send) -> None:
        self.message = await send(embed=self.page(), file=await card_file(self.card()), view=self)

    async def _show(self, interaction: discord.Interaction) -> None:
        self._sync()
        await interaction.response.defer()
        await interaction.edit_original_response(
            embed=self.page(), attachments=[await card_file(self.card())], view=self
        )
        self.notice = ""

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.viewer_id:
            await interaction.response.send_message(
                "Use `mm!img <nome>` para abrir a sua galeria.", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="◀", style=discord.ButtonStyle.secondary)
    async def previous(self, interaction: discord.Interaction, button) -> None:
        self.index -= 1
        await self._show(interaction)

    @discord.ui.button(label="▶", style=discord.ButtonStyle.secondary)
    async def next(self, interaction: discord.Interaction, button) -> None:
        self.index += 1
        await self._show(interaction)

    @discord.ui.button(label="Adicionar", emoji="📷", style=discord.ButtonStyle.primary)
    async def add(self, interaction: discord.Interaction, button) -> None:
        async def save(modal_interaction: discord.Interaction, data: bytes) -> None:
            try:
                added = add_image(int(self.player["id"]), data, self.viewer_id, self.database_path)
            except ImageError as error:
                await modal_interaction.response.send_message(str(error), ephemeral=True)
                return
            self.images = list_images(int(self.player["id"]), self.database_path)
            self.index = next(
                (i for i, image in enumerate(self.images) if image["id"] == added["id"]), 0
            )
            self.notice = (
                "✅ Foto salva e já aplicada na carta."
                if added["main"]
                else "✅ Imagem cadastrada. Toque em ⭐ para usar como principal."
            )
            await self._show(modal_interaction)

        name = self.player.get("display_name") or self.player["name"]
        await interaction.response.send_modal(PhotoUploadModal(name, save))

    @discord.ui.button(label="Usar como principal", emoji="⭐", style=discord.ButtonStyle.success)
    async def make_main(self, interaction: discord.Interaction, button) -> None:
        image = self.current()
        set_main(int(self.player["id"]), int(image["id"]), self.database_path)
        self.images = list_images(int(self.player["id"]), self.database_path)
        self.notice = "⭐ Agora esta é a imagem da carta."
        await self._show(interaction)

    @discord.ui.button(label="Apagar", emoji="🗑️", style=discord.ButtonStyle.danger)
    async def delete(self, interaction: discord.Interaction, button) -> None:
        image = self.current()
        delete_image(int(self.player["id"]), int(image["id"]), self.database_path)
        self.images = list_images(int(self.player["id"]), self.database_path)
        self.index = min(self.index, max(0, len(self.images) - 1))
        self.notice = "🗑️ Imagem apagada."
        await self._show(interaction)

    async def on_timeout(self) -> None:
        if self.message:
            try:
                await self.message.edit(view=None)
            except discord.HTTPException:
                pass


def top_line(index: int, player: dict) -> str:
    owner = f" · 🔒 {player['owner_name']}" if player.get("owner_name") else ""
    name = player.get("display_name") or player["name"]
    return (
        f"`{index:>3}.` **{player['overall']}** — {name} ({player['game_position'] or '—'}){owner}"
    )


class AccountHubView(discord.ui.View):
    """Atalhos do `mm!tu` e da `mm!carteira`: rolar, daily, missões, ativar e inventário.

    Cada botão chama o mesmo código do comando (no cog dele), sem digitar nada."""

    def __init__(self, bot: commands.Bot, owner, status):
        super().__init__(timeout=300)
        self.bot = bot
        self.owner = owner
        self.stored = status.roll_tokens
        self._add("Rolar", "🎲", discord.ButtonStyle.success, self._roll)
        self._add(
            "Daily",
            "🎁",
            discord.ButtonStyle.primary,
            self._daily,
            disabled=status.daily_available_at is not None,
        )
        self._add("Missões", "🎯", discord.ButtonStyle.secondary, self._missions)
        if self.stored:
            self._add(
                f"Ativar {min(self.stored, 10)} rolls",
                "📦",
                discord.ButtonStyle.secondary,
                self._activate,
            )
        self._add("Inventário", "🎒", discord.ButtonStyle.secondary, self._inventory)

    def _add(self, label, emoji, style, callback, *, disabled=False) -> None:
        button = discord.ui.Button(label=label, emoji=emoji, style=style, disabled=disabled)
        button.callback = callback
        self.add_item(button)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner.id:
            await interaction.response.send_message(
                "Esses atalhos são de quem abriu. Use `mm!tu` para os seus.", ephemeral=True
            )
            return False
        return True

    async def _roll(self, interaction: discord.Interaction) -> None:
        await self.bot.get_cog("RollsCog").roll_from_button(interaction)

    async def _daily(self, interaction: discord.Interaction) -> None:
        content, view = self.bot.get_cog("EconomyCog").daily_reply(interaction.user)
        await interaction.response.send_message(content, **({"view": view} if view else {}))

    async def _missions(self, interaction: discord.Interaction) -> None:
        from src.cogs.club import MissionsView

        club = self.bot.get_cog("ClubCog")
        await interaction.response.defer(thinking=True)
        embed, file = await club.missions_message(interaction.user)
        await interaction.followup.send(
            embed=embed, file=file, view=MissionsView(club, interaction.user)
        )

    async def _activate(self, interaction: discord.Interaction) -> None:
        economy = self.bot.get_cog("EconomyCog")
        try:
            result = activate_rolls(
                interaction.user.id,
                interaction.user.display_name,
                economy.database_path,
                min(self.stored, 10),
            )
        except EconomyError as error:
            await interaction.response.send_message(str(error), ephemeral=True)
            return
        text = f"🎲 {result.activated} rolls ativados (agora {result.active_rolls} ativos)."
        if result.claims_gained:
            text += f" ✅ +{result.claims_gained} contratação extra."
        await interaction.response.send_message(text)

    async def _inventory(self, interaction: discord.Interaction) -> None:
        embed, view = self.bot.get_cog("EconomyCog").inventory_menu(interaction.user)
        await interaction.response.send_message(embed=embed, view=view)


class GameCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.database_path = getattr(bot, "database_path", DATABASE_PATH)

    @commands.Cog.listener()
    async def on_command(self, context: commands.Context) -> None:
        if context.author.bot:
            return
        register_user(
            context.author.id,
            context.author.display_name,
            self.database_path,
        )

    @commands.command(name="futae", aliases=["fut.ae", "ajudajogo", "fut"])
    async def futae_help(self, context: commands.Context) -> None:
        embed = discord.Embed(
            title="FUT.AE — comandos do jogo",
            description="Prefixo `mm!` em qualquer combinação de maiúsculas (MM!, Mm!…).",
            color=discord.Color.blurple(),
        )
        for name, value in FUTAE_SECTIONS:
            embed.add_field(name=name, value=value, inline=False)
        embed.set_footer(
            text="Use nomes, não IDs: o ID só aparece quando há homônimos. Acentos são opcionais."
        )
        await context.reply(embed=embed, mention_author=False)

    @commands.command(name="left", aliases=["livres", "restantes"])
    async def collection_left(self, context: commands.Context) -> None:
        counts = get_collection_left(self.database_path)
        lines = [
            f"{label}: **{_thousands(free)}/{_thousands(total)}**"
            for label, free, total in counts["series"]
        ]
        cartola_free, cartola_total = counts["cartola"]
        if cartola_total:
            lines.append(f"🟠 Cartola: **{_thousands(cartola_free)}/{_thousands(cartola_total)}**")
        embed = discord.Embed(
            title="📦 Ainda livres no FUT.AE",
            description="\n".join(lines) or "Nenhum jogador elegível no pool.",
            color=discord.Color.green(),
        )
        embed.add_field(
            name="⚽ Jogadores",
            value=f"**{_thousands(counts['jogadores'])}/{_thousands(counts['jogadores_total'])}**",
            inline=True,
        )
        for kind, (free, total) in free_counts(self.database_path).items():
            label, emoji, *_ = COSMETIC_KINDS[kind]
            embed.add_field(
                name=f"{emoji} {label}s",
                value=f"**{_thousands(free)}/{_thousands(total)}**",
                inline=True,
            )
        embed.set_footer(
            text="livres/total · séries pelo overall do jogo · cosmético livre = ninguém tem"
        )
        await context.reply(embed=embed, mention_author=False)

    @commands.hybrid_command(
        name="scout", aliases=["im", "info"], description="A carta e os dados de um jogador"
    )
    @discord.app_commands.describe(query="Nome do jogador (sugestões enquanto digita)")
    async def player_info(
        self,
        context: commands.Context,
        *,
        query: str,
    ) -> None:
        await context.defer()
        chosen, candidates = find_player(query, self.database_path, limit=15)
        players = [chosen] if chosen else candidates
        items = [] if chosen else search_catalog(query, self.database_path, limit=10)
        results = [{"type": "player", "data": player} for player in players]
        results += [{"type": "catalog", "data": item} for item in items]
        if not results:
            await context.reply(
                "Não encontrei jogador, técnico, estádio, emblema ou item com esse nome.",
                mention_author=False,
            )
            return

        first = results[0]
        if first["type"] == "player":
            embed, file = await player_embed(first["data"])
        else:
            embed, file = catalog_embed(first["data"], self.database_path)
        view = ScoutView(results, self.database_path) if len(results) > 1 else None
        content = (
            f"Encontrei **{len(results)}** resultados; mostrando o mais provável. "
            "Use o menu para ver os outros."
            if view
            else None
        )
        kwargs = {"file": file} if file else {}
        await context.reply(content=content, embed=embed, view=view, mention_author=False, **kwargs)

    @player_info.autocomplete("query")
    async def _scout_names(self, interaction: discord.Interaction, current: str):
        return await player_choices(self.database_path, current)

    @commands.command(name="top")
    async def top_players(
        self,
        context: commands.Context,
        category: str = "jogadores",
    ) -> None:
        entries, total = get_catalog_entries(category, self.database_path, limit=500)
        if not entries:
            await context.reply(
                f"Categoria inválida ou sem itens. Use {TOP_CATEGORIES}.",
                mention_author=False,
            )
            return
        lines = []
        for number, entry in enumerate(entries, 1):
            if "overall" in entry:
                lines.append(
                    f"`{number:>3}.` **{entry['overall']}** — "
                    f"{entry['name']} ({entry['game_position'] or '—'})"
                )
            else:
                lines.append(f"`{number:>3}.` {entry['name']}")
        view = PagedView(f"TOP {category.upper()} ({_thousands(total)})", lines)
        view.message = await context.reply(embed=view.embed(), view=view, mention_author=False)

    @commands.command(name="topi", aliases=["topcartas"])
    async def top_cards(self, context: commands.Context) -> None:
        """O top dos jogadores em cartas: ◀ ▶ passa, Lista mostra o ranking."""
        players = top_player_cards(self.database_path, limit=100)
        if not players:
            await context.reply("Ainda não há jogadores na roleta.", mention_author=False)
            return
        view = CardPagerView(
            players, title="TOP jogadores", line_format=top_line, on_photos=self._open_gallery
        )
        await view.send(lambda **kwargs: context.reply(mention_author=False, **kwargs))

    async def _open_gallery(self, interaction: discord.Interaction, card: dict) -> None:
        """Botão 🖼️ Fotos das cartas: abre a galeria do jogador que está na tela."""
        found = search_players(str(card["id"]), self.database_path, limit=1)
        if not found:
            await interaction.response.send_message("Jogador não encontrado.", ephemeral=True)
            return
        player = found[0]
        view = ImageGalleryView(
            player,
            interaction.user.id,
            can_edit_photos(interaction.user, player),
            self.database_path,
        )
        await interaction.response.defer()
        view.message = await interaction.followup.send(
            embed=view.page(), file=await card_file(view.card()), view=view, wait=True
        )

    @commands.command(name="semfoto", aliases=["semfotos", "fotos"])
    async def missing_photos_panel(
        self, context: commands.Context, scope: str | None = None
    ) -> None:
        """Jogadores sem foto, um por vez, para preencher em sequência."""
        register_user(context.author.id, context.author.display_name, self.database_path)
        mine = normalize_player_name(scope or "") in {"meus", "minhas", "eu", "elenco"}
        view = MissingPhotosView(context.author, self.database_path, mine=mine)
        await view.send(lambda **kwargs: context.reply(mention_author=False, **kwargs))

    @commands.command(name="img", aliases=["imagem", "imagens"])
    async def player_images(self, context: commands.Context, *, query: str | None = None) -> None:
        """Galeria de imagens do jogador; com uma imagem anexada, cadastra uma nova."""
        name = (query or "").strip().strip('"').strip("“”").strip()
        if not name:
            await context.reply(
                "Use `mm!img <jogador>` para ver e adicionar imagens (botão 📷 Adicionar ou "
                "uma imagem anexada no comando). Para achar quem está sem foto: `mm!semfoto`.",
                mention_author=False,
            )
            return
        uploads = [
            attachment
            for attachment in context.message.attachments
            if (attachment.content_type or "").startswith("image/")
        ]

        async def open_gallery(send, player: dict) -> None:
            can_edit = can_edit_photos(context.author, player)
            start = None
            notice = ""
            if uploads:
                # A primeira foto de quem não tem nenhuma é livre: todo mundo ajuda a completar.
                first_photo = not list_images(int(player["id"]), self.database_path)
                if not (can_edit or first_photo):
                    await send(
                        content=(
                            f"**{player_label(player)}** já tem foto: só o dono dele (ou a "
                            "moderação) adiciona outras. Para ajudar com quem está sem foto, "
                            "use `mm!semfoto`."
                        ),
                        embed=None,
                        view=None,
                    )
                    return
                try:
                    data = await uploads[0].read()
                    added = add_image(
                        int(player["id"]), data, context.author.id, self.database_path
                    )
                except (ImageError, discord.HTTPException) as error:
                    message = (
                        str(error)
                        if isinstance(error, ImageError)
                        else "Não consegui baixar a imagem."
                    )
                    await send(content=message, embed=None, view=None)
                    return
                start = added["id"]
                notice = (
                    "✅ Imagem cadastrada e já aplicada na carta (o jogador não tinha foto)."
                    if added["main"]
                    else "✅ Imagem cadastrada. Toque em ⭐ para usar como principal."
                )
            view = ImageGalleryView(
                player, context.author.id, can_edit, self.database_path, start=start
            )
            view.notice = notice
            view.message = await send(
                content=None, embed=view.page(), file=await card_file(view.card()), view=view
            )
            view.notice = ""

        player, candidates = find_player(name, self.database_path)
        if player is not None:
            await open_gallery(
                lambda **kwargs: context.reply(mention_author=False, **kwargs), player
            )
            return
        if not candidates:
            await context.reply(f"Não achei **{name[:60]}**.", mention_author=False)
            return

        async def chosen(interaction: discord.Interaction, picked: dict) -> None:
            await interaction.response.defer()
            await open_gallery(
                lambda **kwargs: interaction.edit_original_response(
                    **{
                        ("attachments" if key == "file" else key): (
                            [value] if key == "file" else value
                        )
                        for key, value in kwargs.items()
                    }
                ),
                picked,
            )

        await context.reply(
            ambiguity_text(name, candidates),
            view=PlayerChoiceView(context.author.id, candidates, chosen),
            mention_author=False,
        )

    @commands.command(name="elenco", aliases=["mm", "squad"])
    async def roster(
        self,
        context: commands.Context,
        *,
        query: str | None = None,
    ) -> None:
        target = context.message.mentions[0] if context.message.mentions else context.author
        filter_text = re.sub(r"<@!?\d+>", "", query or "").strip()
        register_user(target.id, target.display_name, self.database_path)
        filters: dict[str, object] = {}
        lineup_filter = normalize_player_name(filter_text)
        if lineup_filter in {"titulares", "titular", "reservas", "reserva"}:
            filter_text_for_query = ""
        else:
            lineup_filter = ""
            filter_text_for_query = filter_text
        if filter_text_for_query:
            position = re.fullmatch(r"(?:posicao|posição)\s+([A-Za-z]{2,3})", filter_text, re.I)
            overall = re.fullmatch(
                r"(?:overall\s*)?(>=|<=|>|<|=)?\s*(\d{1,3})",
                filter_text,
                re.I,
            )
            category = re.fullmatch(r"(clube|liga)\s+(.+)", filter_text, re.I)
            if position:
                filters["position"] = position.group(1).upper()
            elif overall:
                operator = overall.group(1) or "="
                rating = max(1, min(100, int(overall.group(2))))
                if operator == ">=":
                    filters["min_overall"] = rating
                elif operator == ">":
                    filters["min_overall"] = min(100, rating + 1)
                elif operator == "<=":
                    filters["max_overall"] = rating
                elif operator == "<":
                    filters["max_overall"] = max(1, rating - 1)
                else:
                    filters["min_overall"] = rating
                    filters["max_overall"] = rating
            elif category:
                filters[
                    "club_query" if category.group(1).casefold() == "clube" else "league_query"
                ] = category.group(2)
            else:
                await context.reply(
                    "Filtro inválido. Use `titulares`, `reservas`, `posicao CA`, "
                    "`overall >= 80`, `clube nome` ou `liga nome`.",
                    mention_author=False,
                )
                return
        try:
            players = list_owned_players(
                target.id,
                self.database_path,
                limit=1000,
                sort_by="overall",
                **filters,
            )
        except ValueError as error:
            await context.reply(str(error), mention_author=False)
            return
        lineup = {int(player["id"]): player for player in get_lineup(target.id, self.database_path)}
        for player in players:
            starter = lineup.get(int(player["id"]))
            player["lineup_position"] = starter["position"] if starter else None
            player["slot_order"] = starter["slot_order"] if starter else None
        if lineup_filter:
            starters = lineup_filter.startswith("titular")
            players = [player for player in players if bool(player["lineup_position"]) == starters]
        if not players:
            await context.reply(
                f"Nenhum jogador encontrado no elenco de {target.display_name}.",
                mention_author=False,
            )
            return
        starters = sum(1 for player in players if player["lineup_position"])
        title = f"Elenco de {target.display_name} ({len(players)} · ⭐ {starters} titulares)"
        if filter_text:
            title += f" · {filter_text}"
        view = CardPagerView(
            players,
            title=title,
            line_format=roster_line,
            sorts=ROSTER_SORTS,
            on_photos=self._open_gallery,
        )
        await view.send(lambda **kwargs: context.reply(mention_author=False, **kwargs))

    @commands.command(name="carteira", aliases=["saldo", "wallet", "bal"])
    async def wallet(self, context: commands.Context) -> None:
        status = get_account_status(
            context.author.id, context.author.display_name, self.database_path
        )
        embed = discord.Embed(
            title=f"🪙 Carteira de {context.author.display_name}",
            description=f"# {icons.memilets()} {_thousands(status.memilets)} Memilets",
            color=discord.Color.gold(),
        )
        embed.set_thumbnail(url=context.author.display_avatar.url)
        embed.add_field(name="⚽ Elenco", value=f"{status.owned_players} jogadores", inline=True)
        embed.add_field(
            name="🎲 Rolls",
            value=(
                f"{status.rolls_left}/{status.roll_capacity} no ciclo"
                + (f" + {status.active_rolls} ativos" if status.active_rolls else "")
                + (
                    f"\n{status.roll_tokens} guardados (`mm!ativar <quantidade>`)"
                    if status.roll_tokens
                    else ""
                )
            ),
            inline=True,
        )
        embed.add_field(
            name="💫 Wishes",
            value=(
                f"{status.wishes}/{status.wish_capacity} · "
                f"⭐ {status.star_wishes}/{status.star_wish_capacity}"
            ),
            inline=True,
        )
        embed.add_field(
            name="⏱️ Claim",
            value=_timestamp(status.claim_available_at)
            + (f"\n+{status.bonus_claims} extra(s)" if status.bonus_claims else ""),
            inline=True,
        )
        embed.add_field(
            name="🎁 Daily",
            value=f"{_timestamp(status.daily_available_at)}\nmáx. {status.daily_cap}",
            inline=True,
        )
        embed.add_field(
            name="📦 Crate",
            value=f"{icons.memilets(status.crate_price)}",
            inline=True,
        )
        if status.wish_protection:
            embed.add_field(
                name="🛡️ Proteção de wish", value=f"{status.wish_protection} s", inline=True
            )
        embed.set_footer(text="mm!upgrade e mm!construir para evoluir · mm!tu para ver os timers")
        await context.reply(
            embed=embed, view=AccountHubView(self.bot, context.author, status), mention_author=False
        )

    @commands.command(name="tu", aliases=["timers", "timer"])
    async def timers(self, context: commands.Context) -> None:
        status = get_account_status(
            context.author.id, context.author.display_name, self.database_path
        )
        extras = f" (+{status.active_rolls} ativos)" if status.active_rolls else ""
        if status.instant_rolls:
            extras += f" · {status.instant_rolls} instantâneo(s) da crate"
        lines = [
            f"🎲 **{status.rolls_left}/{status.roll_capacity} rolls**{extras} · "
            f"renovam {_timestamp(status.rolls_reset_at)}",
            f"⏱️ Claim: {_timestamp(status.claim_available_at)}"
            + (f" · +{status.bonus_claims} extra(s)" if status.bonus_claims else ""),
            f"🎁 Daily: {_timestamp(status.daily_available_at)}",
            f"{icons.memilets()} Saldo: **{_thousands(status.memilets)} Memilets**",
        ]
        if status.roll_tokens:
            lines.append(
                f"📦 {status.roll_tokens} rolls guardados · ative com `mm!ativar <quantidade>`"
            )
        embed = discord.Embed(
            title=f"Timers de {context.author.display_name}",
            description="\n".join(lines),
            color=discord.Color.blurple(),
        )
        await context.reply(
            embed=embed, view=AccountHubView(self.bot, context.author, status), mention_author=False
        )


class StandaloneHelpCog(commands.Cog):
    """Menu geral do MeMi BOT, com o FUT.AE na segunda página."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="help", aliases=["ajuda", "comandos"])
    async def general_help(self, context: commands.Context, command: str = "") -> None:
        if command:
            embed = command_help_embed(self.bot, command)
            await context.reply(
                embed=embed,
                content=None if embed else f"Não achei o comando `{command[:30]}`.",
                mention_author=False,
            )
            return
        view = PagedView(
            "MeMi BOT — ajuda",
            [],
            color=discord.Color.blurple(),
            footer="Use os botões para navegar · mm!ajuda <comando> mostra o detalhe.",
        )
        view.pages = [[page] for page in GENERAL_HELP_PAGES]
        view.index = 0
        view._sync_buttons()
        view.message = await context.reply(
            embed=view.embed(),
            view=view,
            mention_author=False,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(GameCog(bot))
    if bot.get_command("help") is None:
        await bot.add_cog(StandaloneHelpCog(bot))
