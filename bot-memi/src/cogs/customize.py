"""Painel de personalização do time (`mm!personalizar` ou o botão no `mm!time`).

Tudo num lugar só, com a prévia do cartão do clube atualizando a cada mudança:
nome e cor (paleta pronta ou código hex), escudo próprio (a imagem é enviada no próprio
formulário) e os cosméticos que a pessoa tem: emblema, uniforme, estádio, técnico, liga e
mascote, escolhidos em menus, com a opção de tirar.
"""

from __future__ import annotations

import discord

from src.cosmetics import (
    COSMETIC_KINDS,
    CosmeticError,
    equip_key,
    equipped_name,
    list_owned,
    unequip,
)
from src.crests import MAX_BYTES, CrestError, remove_custom_crest, save_custom_crest
from src.db import normalize_player_name
from src.narration import team_dot
from src.teams import TeamError, get_lineup, get_team, set_team_field

OPTION_LIMIT = 25
PALETTE = (
    ("Verde", "#2E8B57"),
    ("Vermelho", "#C0392B"),
    ("Grená", "#7B1E2B"),
    ("Azul", "#2E86C1"),
    ("Celeste", "#5DADE2"),
    ("Marinho", "#1F3A93"),
    ("Preto", "#1C1C1C"),
    ("Branco", "#ECF0F1"),
    ("Amarelo", "#F1C40F"),
    ("Dourado", "#C9A227"),
    ("Laranja", "#E67E22"),
    ("Roxo", "#8E44AD"),
    ("Rosa", "#E84393"),
    ("Verde-limão", "#7CB342"),
)


def current_value(team: dict, kind: str, database_path) -> str | None:
    column = COSMETIC_KINDS[kind][2]
    return equipped_name(kind, team.get(column), database_path)


class NameModal(discord.ui.Modal):
    def __init__(self, panel: "CustomizeView", current: str):
        super().__init__(title="Nome do time")
        self.panel = panel
        self.name = discord.ui.TextInput(
            label="Nome", default=current[:50], min_length=1, max_length=50
        )
        self.add_item(self.name)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.panel.apply(
            interaction, lambda: self.panel.set_field("team_name", str(self.name.value))
        )


class ColorModal(discord.ui.Modal):
    def __init__(self, panel: "CustomizeView", current: str):
        super().__init__(title="Cor do time")
        self.panel = panel
        self.color = discord.ui.TextInput(
            label="Código da cor (ex.: #2E8B57)", default=current, min_length=4, max_length=7
        )
        self.add_item(self.color)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        value = str(self.color.value).strip()
        value = value if value.startswith("#") else f"#{value}"
        await self.panel.apply(interaction, lambda: self.panel.set_field("color", value))


class CrestModal(discord.ui.Modal):
    """O escudo vai anexado no próprio formulário."""

    def __init__(self, panel: "CustomizeView"):
        super().__init__(title="Escudo do time")
        self.panel = panel
        self.upload = discord.ui.FileUpload(max_values=1)
        self.add_item(
            discord.ui.Label(
                text="Imagem do escudo",
                description="PNG com fundo transparente fica melhor (até 8 MB).",
                component=self.upload,
            )
        )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        files = list(self.upload.values or [])
        if not files:
            await interaction.response.send_message("Nenhuma imagem enviada.", ephemeral=True)
            return
        if files[0].size > MAX_BYTES:
            await interaction.response.send_message(
                "A imagem passa de 8 MB. Envie uma menor.", ephemeral=True
            )
            return
        data = await files[0].read()
        await self.panel.apply(
            interaction,
            lambda: save_custom_crest(self.panel.owner.id, data, self.panel.database_path),
        )


class SearchModal(discord.ui.Modal):
    def __init__(self, panel: "CustomizeView"):
        super().__init__(title="Buscar nos seus itens")
        self.panel = panel
        self.query = discord.ui.TextInput(
            label="Parte do nome (vazio mostra todos)", required=False, max_length=60
        )
        self.add_item(self.query)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        self.panel.query = str(self.query.value or "").strip()
        await self.panel.refresh(interaction)


class CustomizeView(discord.ui.View):
    """Painel com menus e botões; só o dono do time mexe."""

    def __init__(self, owner: discord.abc.User, database_path):
        super().__init__(timeout=600)
        self.owner = owner
        self.database_path = database_path
        self.kind: str | None = None
        self.query = ""
        self.notice = ""
        self.items: list[dict] = []
        self.message: discord.Message | None = None
        self._build()

    # Estado

    def team(self) -> dict:
        return get_team(self.owner.id, self.database_path) or {}

    def set_field(self, field: str, value: str) -> None:
        set_team_field(self.owner.id, field, value, self.database_path)

    # Interface

    def _build(self) -> None:
        self.clear_items()
        team = self.team()
        palette = discord.ui.Select(
            placeholder="🎨 Cor do time (paleta)",
            options=[
                discord.SelectOption(
                    label=name,
                    value=code,
                    description=code,
                    emoji=team_dot(code),
                    default=(team.get("color") or "").upper() == code,
                )
                for name, code in PALETTE
            ],
            row=0,
        )
        palette.callback = self._palette
        self.add_item(palette)

        kinds = discord.ui.Select(
            placeholder="👕 Trocar emblema, uniforme, estádio, técnico, liga ou mascote",
            options=[
                discord.SelectOption(
                    label=label,
                    value=kind,
                    emoji=emoji,
                    description=(current_value(team, kind, self.database_path) or "nada equipado")[
                        :100
                    ],
                    default=kind == self.kind,
                )
                for kind, (label, emoji, _, _) in COSMETIC_KINDS.items()
            ],
            row=1,
        )
        kinds.callback = self._choose_kind
        self.add_item(kinds)
        self.add_item(self._items_select(team))

        for label, emoji, callback, disabled in (
            ("Nome", "✏️", self._name, False),
            ("Cor (código)", "🎨", self._color, False),
            ("Escudo", "🛡️", self._crest, False),
            ("Tirar escudo", "🗑️", self._remove_crest, not team.get("custom_logo_path")),
            ("Buscar item", "🔎", self._search, self.kind is None),
        ):
            button = discord.ui.Button(label=label, emoji=emoji, row=3, disabled=disabled)
            button.callback = callback
            self.add_item(button)
        done = discord.ui.Button(
            label="Concluir", emoji="✅", style=discord.ButtonStyle.success, row=4
        )
        done.callback = self._done
        self.add_item(done)

    def _items_select(self, team: dict) -> discord.ui.Select:
        if self.kind is None:
            return discord.ui.Select(
                placeholder="Escolha acima o que trocar",
                options=[discord.SelectOption(label="—", value="none")],
                disabled=True,
                row=2,
            )
        label, emoji, column, stores_path = COSMETIC_KINDS[self.kind]
        owned = list_owned(self.owner.id, self.kind, self.database_path)
        equipped = team.get(column)
        wanted = normalize_player_name(self.query)
        visible = [
            item for item in owned if not wanted or wanted in normalize_player_name(item["name"])
        ]
        is_equipped = (
            lambda item: (item["image_path"] if stores_path else item["name"]) == equipped
        )  # noqa: E731
        visible.sort(key=lambda item: (not is_equipped(item), item["name"].casefold()))
        self.items = visible[: OPTION_LIMIT - 1]
        options = [
            discord.SelectOption(
                label="Nenhum (tirar)", value="__none__", emoji="🚫", default=not equipped
            )
        ] + [
            discord.SelectOption(
                label=item["name"][:100], value=str(index), emoji=emoji, default=is_equipped(item)
            )
            for index, item in enumerate(self.items)
        ]
        hint = f" ({len(visible)} · 🔎 para buscar)" if len(visible) > OPTION_LIMIT - 1 else ""
        if not owned:
            placeholder = f"Você ainda não tem {label.lower()}s · saem nas crates"
        else:
            placeholder = f"{emoji} Seus {label.lower()}s{hint}"
        select = discord.ui.Select(placeholder=placeholder[:150], options=options, row=2)
        select.callback = self._choose_item
        return select

    def embed(self) -> discord.Embed:
        team = self.team()
        color = team.get("color") or "#2E8B57"
        crest = (
            "imagem própria"
            if team.get("custom_logo_path")
            else (current_value(team, "emblema", self.database_path) or "monograma com as iniciais")
        )
        lines = [
            f"**Nome:** {team.get('team_name') or '—'}",
            f"**Cor:** {team_dot(color)} `{color}`",
            f"**Escudo:** {crest}",
        ]
        for kind in ("uniforme", "estadio", "tecnico", "liga", "mascote"):
            label, emoji, *_ = COSMETIC_KINDS[kind]
            lines.append(
                f"{emoji} **{label}:** {current_value(team, kind, self.database_path) or '—'}"
            )
        embed = discord.Embed(
            title="🎨 Personalizar time",
            description="\n".join(lines) + (f"\n\n{self.notice}" if self.notice else ""),
            color=discord.Color.from_str(color),
        )
        embed.set_image(url="attachment://clube.png")
        embed.set_footer(
            text="Cosméticos novos saem nas crates (mm!crates) · a prévia é o cartão do clube"
        )
        return embed

    async def file(self) -> discord.File:
        from src.cogs.team import club_file  # o cog do time importa este painel

        team = self.team()
        lineup = get_lineup(self.owner.id, self.database_path)
        return await club_file(self.owner.id, team, lineup, self.database_path)

    async def refresh(self, interaction: discord.Interaction) -> None:
        self._build()
        await interaction.response.defer()
        file = await self.file()
        await interaction.edit_original_response(embed=self.embed(), attachments=[file], view=self)

    async def apply(self, interaction: discord.Interaction, change) -> None:
        """Aplica a mudança e atualiza a prévia; erro vira aviso no próprio painel."""
        try:
            change()
            self.notice = "✅ Atualizado."
        except (TeamError, CosmeticError, CrestError, ValueError) as error:
            self.notice = f"⚠️ {error}"
        await self.refresh(interaction)

    # Callbacks

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner.id:
            await interaction.response.send_message(
                "Esse painel não é seu. Use `mm!personalizar` para o seu time.", ephemeral=True
            )
            return False
        return True

    async def _palette(self, interaction: discord.Interaction) -> None:
        value = interaction.data["values"][0]
        await self.apply(interaction, lambda: self.set_field("color", value))

    async def _choose_kind(self, interaction: discord.Interaction) -> None:
        self.kind = interaction.data["values"][0]
        self.query = ""
        self.notice = ""
        await self.refresh(interaction)

    async def _choose_item(self, interaction: discord.Interaction) -> None:
        value = interaction.data["values"][0]
        kind = self.kind
        if value == "__none__":
            await self.apply(interaction, lambda: unequip(self.owner.id, kind, self.database_path))
            return
        key = self.items[int(value)]["key"]
        await self.apply(
            interaction, lambda: equip_key(self.owner.id, kind, key, self.database_path)
        )

    async def _name(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(NameModal(self, self.team().get("team_name") or ""))

    async def _color(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(
            ColorModal(self, self.team().get("color") or "#2E8B57")
        )

    async def _crest(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(CrestModal(self))

    async def _remove_crest(self, interaction: discord.Interaction) -> None:
        await self.apply(
            interaction, lambda: remove_custom_crest(self.owner.id, self.database_path)
        )

    async def _search(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(SearchModal(self))

    async def _done(self, interaction: discord.Interaction) -> None:
        self.stop()
        self.notice = ""
        await interaction.response.edit_message(embed=self.embed(), view=None)

    async def on_timeout(self) -> None:
        if self.message:
            try:
                await self.message.edit(view=None)
            except discord.HTTPException:
                pass
