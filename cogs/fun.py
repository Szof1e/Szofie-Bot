"""Android 21's magic 8-ball."""

from __future__ import annotations
import random
from typing import Optional
import discord
from discord import app_commands
from discord.ext import commands
from szofie import ui

EIGHTBALL_ANSWERS = [
    ("Yes. Obviously. Did you doubt me, morsel?", True),
    ("The candy says yes — and the candy never lies.", True),
    ("Mm. It is certain. I can taste it.", True),
    ("Yes, sweet thing. Go on, then.", True),
    ("Without a doubt. Now stop pestering me.", True),
    ("Ask again later. I'm busy digesting.", None),
    ("Hazy. The lab is full of smoke today.", None),
    ("Perhaps. Perhaps I simply don't care to say.", None),
    ("The answer is marinating. Come back hungry.", None),
    ("No. And I'd stop asking, if I were you.", False),
    ("Absolutely not, little snack.", False),
    ("My sources — mostly the ones I devoured — say no.", False),
    ("Don't count on it. Count your fingers instead.", False),
    ("No. The stars, and my appetite, agree.", False),
]


class Fun(commands.Cog):
    """A small, standalone 8-ball game."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="8ball", description="Ask Android 21's magic 8-ball a question.")
    @app_commands.describe(question="What you want to ask")
    async def eightball(self, interaction: discord.Interaction, question: str) -> None:
        cfg = self.bot.config.for_guild(interaction.guild_id) if interaction.guild_id else None
        color = cfg.color if cfg else 11894492
        answer, verdict = random.choice(EIGHTBALL_ANSWERS)
        tone = {True: ui.COLOR_OK, False: ui.COLOR_BAD, None: ui.COLOR_WARN}[verdict]
        embed = ui.base_embed(title="🎱 The Majin 8-ball", color=tone or color)
        embed.add_field(name="You asked", value=question[:1000], inline=False)
        embed.add_field(name="Android 21 says", value=answer, inline=False)
        await ui.respond(interaction, embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Fun(bot))
