"""Interface do `mm!desafio`: o painel com escada, chefões e desafio livre."""

from __future__ import annotations

import discord

from src import icons
from src.pve import (
    ACHIEVEMENTS,
    CAMPAIGN_STEP_PRIZE,
    CAMPAIGNS,
    DAILY_BOSS_PRIZE,
    DAILY_BOSS_TRIES,
    WEEKLY_BOSS_PRIZE,
    WEEKLY_BOSS_TRIES,
    daily_boss,
    free_prize,
    ladder,
    ladder_prize,
    legend_teams,
    campaign_progress,
    earned_achievements,
    pve_status,
    weekly_boss,
)

PVE_INTRO = (
    "Enfrente times lendários: os grandes clubes brasileiros de todos os tempos, seleções "
    "lendárias e a Seleção Mundial. Suba a **escada** degrau por degrau, encare os "
    "**chefões** do dia e da semana, jogue as **campanhas** ou desafie quem quiser no "
    "**desafio livre**."
)


def hub_embed(user, database_path, team_strength: int | None) -> discord.Embed:
    status = pve_status(user.id, database_path)
    embed = discord.Embed(
        title="🏟️ Desafios lendários",
        description=f"*{PVE_INTRO}*",
        color=discord.Color.from_rgb(230, 181, 74),
    )
    if team_strength:
        embed.description += f"\n\nSeu time: força **{team_strength}**."
    if status.ladder_step < status.ladder_total:
        nxt = ladder(database_path)[status.ladder_step]
        step = (
            f"Degrau **{status.ladder_step + 1}/{status.ladder_total}**: {nxt.label} "
            f"(força {nxt.power})\nPrêmio: {icons.memilets(ladder_prize(status.ladder_step))}"
            + (" + 📦 caixa" if (status.ladder_step + 1) % 5 == 0 else "")
        )
    else:
        step = "🏆 Escada completa!"
    embed.add_field(name="🪜 Escada", value=step, inline=False)
    boss = daily_boss()
    daily = (
        "✅ Vencido hoje"
        if status.daily_won
        else f"{DAILY_BOSS_TRIES - status.daily_tries}/{DAILY_BOSS_TRIES} tentativa"
    )
    embed.add_field(
        name="☀️ Chefão do dia",
        value=f"{boss.label} (força {boss.power}) · {daily}\n"
        f"Prêmio: {icons.memilets(DAILY_BOSS_PRIZE)} + 🟠 caixa épica",
        inline=False,
    )
    weekly = weekly_boss()
    tries = (
        "✅ Derrubado nesta semana"
        if status.weekly_won
        else f"{WEEKLY_BOSS_TRIES - status.weekly_tries}/{WEEKLY_BOSS_TRIES} tentativas"
    )
    embed.add_field(
        name="🗓️ Chefão da semana",
        value=f"{weekly.label} (força {weekly.power}) · {tries}\n"
        f"Prêmio: {icons.memilets(WEEKLY_BOSS_PRIZE)} + 🎴 pacote épico",
        inline=False,
    )
    embed.add_field(
        name="⚔️ Desafio livre",
        value="Escolha um time lendário no menu: o prêmio sai na primeira vitória do dia "
        "contra cada um.",
        inline=False,
    )
    embed.set_footer(text="Contra lendas só a vitória conta · lendas jogam acima do overall")
    return embed


class PveHubView(discord.ui.View):
    def __init__(self, cog, owner, database_path):
        super().__init__(timeout=300)
        self.cog = cog
        self.owner = owner
        status = pve_status(owner.id, database_path)
        self._button(
            "Escada",
            "🪜",
            discord.ButtonStyle.success,
            "escada",
            status.ladder_step >= status.ladder_total,
        )
        self._button(
            "Chefão do dia",
            "☀️",
            discord.ButtonStyle.primary,
            "dia",
            status.daily_won or status.daily_tries >= DAILY_BOSS_TRIES,
        )
        self._button(
            "Chefão da semana",
            "🗓️",
            discord.ButtonStyle.danger,
            "semana",
            status.weekly_won or status.weekly_tries >= WEEKLY_BOSS_TRIES,
        )
        campaigns = discord.ui.Button(
            label="Campanhas", emoji="📜", style=discord.ButtonStyle.secondary
        )
        campaigns.callback = self._campaigns
        self.add_item(campaigns)
        achievements = discord.ui.Button(
            label="Conquistas", emoji="🏅", style=discord.ButtonStyle.secondary
        )
        achievements.callback = self._achievements
        self.add_item(achievements)
        self.database_path = database_path
        teams = list(legend_teams())
        select = discord.ui.Select(
            placeholder="⚔️ Desafio livre: escolha um time lendário",
            options=[
                discord.SelectOption(
                    label=team.name[:100],
                    emoji=team.emoji,
                    description=f"Força {team.power} · prêmio {free_prize(team):,}".replace(
                        ",", "."
                    ),
                    value=team.key,
                )
                for team in teams[:25]
            ],
            row=1,
        )
        select.callback = self._free
        self.add_item(select)

    def _button(self, label, emoji, style, kind, disabled) -> None:
        button = discord.ui.Button(label=label, emoji=emoji, style=style, disabled=disabled)

        async def play(interaction: discord.Interaction) -> None:
            await self.cog.start_pve(interaction, kind, None)

        button.callback = play
        self.add_item(button)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner.id:
            await interaction.response.send_message(
                "Esse painel é de quem abriu. Use `mm!desafio` para o seu.", ephemeral=True
            )
            return False
        return True

    async def _campaigns(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(
            embed=campaigns_embed(interaction.user, self.database_path),
            view=CampaignView(self.cog, self.owner),
        )

    async def _achievements(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(
            embed=achievements_embed(interaction.user, self.database_path), ephemeral=True
        )

    async def _free(self, interaction: discord.Interaction) -> None:
        await self.cog.start_pve(interaction, "livre", interaction.data["values"][0])


def campaigns_embed(user, database_path) -> discord.Embed:
    progress = campaign_progress(user.id, database_path)
    embed = discord.Embed(
        title="📜 Campanhas lendárias",
        description=f"Cada vitória numa campanha paga {icons.memilets(CAMPAIGN_STEP_PRIZE)}.",
        color=discord.Color.from_rgb(230, 181, 74),
    )
    for key, campaign in CAMPAIGNS.items():
        step, done = progress[key]
        teams = campaign.teams()
        nxt = teams[step]
        lines = [
            campaign.description,
            f"Próximo: **{campaign.stage(step)}** contra {nxt.label} (força {nxt.power}) · "
            f"{step}/{len(teams)}",
            f"Prêmio final: {icons.memilets(campaign.prize)}"
            + (f" + {campaign.epic_packs} 🎴 pacote(s) épico(s)" if campaign.epic_packs else ""),
        ]
        if done:
            lines.append(f"✅ Completa {done}x")
        embed.add_field(
            name=f"{campaign.emoji} {campaign.name}", value="\n".join(lines), inline=False
        )
    return embed


def achievements_embed(user, database_path) -> discord.Embed:
    have = earned_achievements(user.id, database_path)
    lines = [
        f"{'✅' if key in have else '⬜'} {emoji} **{name}** · {goal} · {prize:,}".replace(",", ".")
        for key, (emoji, name, goal, prize, _) in ACHIEVEMENTS.items()
    ]
    return discord.Embed(
        title=f"🏅 Conquistas · {len(have)}/{len(ACHIEVEMENTS)}",
        description="\n".join(lines),
        color=discord.Color.from_rgb(230, 181, 74),
    )


class CampaignView(discord.ui.View):
    """Um botão por campanha: joga a próxima partida dela."""

    def __init__(self, cog, owner):
        super().__init__(timeout=300)
        self.cog = cog
        self.owner = owner
        for key, campaign in CAMPAIGNS.items():
            button = discord.ui.Button(
                label=campaign.name, emoji=campaign.emoji, style=discord.ButtonStyle.primary
            )
            button.callback = self._player(key)
            self.add_item(button)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner.id:
            await interaction.response.send_message(
                "Essas campanhas são de quem abriu. Use `mm!desafio`.", ephemeral=True
            )
            return False
        return True

    def _player(self, key: str):
        async def play(interaction: discord.Interaction) -> None:
            await self.cog.start_pve(interaction, f"campanha:{key}", None)

        return play
