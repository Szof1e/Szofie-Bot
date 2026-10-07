"""Help, status, and owner utilities."""

from __future__ import annotations
import logging
import platform
import time
from typing import Dict, List, Optional, Tuple
import discord
from discord import app_commands
from discord.ext import commands
from szofie import __version__, ui

log = logging.getLogger("szofie.general")
CATEGORIES: Dict[str, Dict[str, str]] = {
    "Channel Security": {
        "emoji": "💥",
        "blurb": "Recreate a channel as an identical empty copy, with destructive safeguards.",
        "commands": "`/nuke [channel] [reason]`",
    },
    "Fun": {"emoji": "🎲", "blurb": "Ask the Majin 8-ball a question.", "commands": "`/8ball`"},
    "Fishing": {
        "emoji": "🎣",
        "blurb": "Cast for fish, sell the haul before it rots, upgrade and enchant your rod (rods in /shop, enchant catalog in `/enchants`).\n🌊 **The Abyss** — equip a mythic or abyssal rod and `/dive` into the deep to hunt void species and the legendary **Voidcaller** white whale. Complete the Codex to unlock a two-draw **Deep Expedition** that consumes Abyssal Debris. Use **Contracts & research** on `/fish`, `/dive` or `/bestiary` for two daily deliveries, civilian biosensors, habitat materials and optional trophy archives.",
        "commands": "`/fish` `/dive` `/autofish` `/bucket` `/bestiary` `/sell` `/sellall` `/rod` `/enchant` `/enchants`",
    },
    "Countries": {
        "emoji": "🌍",
        "blurb": "Build a realm from fixed prestige GDP. Your first country may be purchased peacefully; every additional country must be conquered with a loaded offensive vehicle and a burned war chest. Every territory produces at its full rate and net income is delivered to its ruler's wallet automatically.",
        "commands": "`/country guide` `/country atlas` `/country inspect` `/country claim` `/country portfolio`\n`/country develop` `/country reinforce` `/country invade` `/country leaderboard` `/country abandon`",
    },
    "Solar System": {
        "emoji": "🪐",
        "blurb": "Start exploring with a Drake Cutlass, field expeditions and a discovery journal. Use an operational Imperial Star Destroyer for bulk cargo, automatic local mining, colony specialisations, terraform settlements and claim colonial GDP. An X-wing can intercept loading transports; attack mode can bombard a single world. Ordinary mining, salvage, recon, escort and precision-bombing ships use `/space fleet`; start with the public button-guided walkthrough. **Civilian contracts** on `/space status` or `/space journal` rewards new survey/supply/industry arrivals. Optional escorts on `/isd launch` and `/deathstar deploy` screen a finite number of component interceptors.",
        "commands": "`/exploration` `/space guide` `/space fleet guide` `/space fleet status` `/space fleet mission` `/space fleet payload` `/space fleet escort` `/space fleet raid` `/space fleet repair` `/space atlas` `/space status` `/space build` `/space expedition` `/space journal` `/space specialise` `/isd guide` `/deathstar guide`",
    },
    "Death Star": {
        "emoji": "🌑",
        "blurb": "DS-1 is a 1-septillion, eight-section artificial-moon megaproject. Build three sections simultaneously, survive public orbital counterplay, then develop 5–50 quintillion/day of automatic wallet production. Its superlaser destroys one celestial body and resets its sovereign's civilisation; Raven Rock contents survive. Build `/deathstar squadron` in advance (10 quintillion, 12h), then `/deathstar intercept window:<DS-ID>` during deployment/assembly/firing. Earth and the Sun cannot be targeted. Start with the public button-paged guide.",
        "commands": "`/deathstar guide` `/deathstar status` `/deathstar build` `/deathstar squadron` `/deathstar windows` `/deathstar intercept`",
    },
    "Starboard": {
        "emoji": "⭐",
        "blurb": "React ⭐ to feature a message in a highlights channel.",
        "commands": "React ⭐ · set it up with `/config set starboard.channel`",
    },
    "Economy": {
        "emoji": "🍩",
        "blurb": "Donuts, games, a bank and a shop.\n💼 **Careers** — choose a profession with `/job apply`, complete decision-based shifts through `/work`, and earn promotions, salary growth and a daily payday. Browse `/jobs`; work results show the next licence and gates, while `/job licences` also offers paid Android 21 certification.\n🏆 **Seasons** — climb the net-worth ladder each season with `/season`; the champion is recorded in `/season view:history`.\n🎉 **Random events** rotate through economic, fishing, casino, warfare and community categories. `/event` shows the active effect and next window.\n🚀 **ICBMs** — the doomsday option. Build one with `/icbm build`, then `/icbm launch` to vaporize a chunk of a rival's donuts *and* items. Nobody profits — it just burns. `/icbm silo` tracks your stockpile.\n🛡 **Radar AA** — shoot ICBMs and light strategic aircraft down. `/aa shield` buys a timed ICBM window and strengthens loaded rockets against U-2s, MQ-9Bs, Apaches, Comanches and A-10Cs; `/aa build` and `/aa load` supply the battery. Check `/aa status`.\n✈️ **Strategic vehicles** — total-strike and runway-suppression bombers, reconnaissance, EMRG ships, submarines, Apache/Comanche ammunition raids, drones, strike fighters, multirole ground-attack aircraft and their dedicated counters live under `/vehicle`; `/deepvault` hides wealth and `/arsenal` privately pages through all forces, orbital projects and interception craft. Use `/warfare` for the public field manual.\n🦅 **Fifth-generation jets** — F-22 patrols protect you or an ally; F-35 links improve a recon-backed vehicle strike; J-20 hunts aircraft; Su-57 raids selected targets with an optional second pass. `/vehicle load` buys mission packages: each sortie/pass spends one. Check `/warfare` for recipes and counters.\n⚡ **Project THOR** — fabricate and launch three orbital modules, assemble them, resupply kinetic rods, service the platform, and defend module launches with GBI/GBI Block II or fired rods with Aegis BMD under `/thor`. Aegis fires one SM-3 per incoming rod; spare missiles are for later strikes, not extra attempts. Raven Rock recovery storage lives under `/continuity`. Start with `/thor guide`.\n🛰️ **NYX** — build a counter-intelligence satellite with `/space build craft:NYX`. Ghost Protocol blocks recon for three hours and limits a landed THOR strike to one random damage category. Start with `/space fleet guide craft:NYX`.\n🚩 **Imperial Star Destroyer** — the 15 quintillion, six-component server-reset megaproject lives under `/isd`, with public B-wing counterplay. Start with `/isd guide`; use `/exploration` for its Solar System role.\n🤝 **Coalitions** — up to ten players can share private intelligence and permanently transfer donuts, supplies, vehicles and ammunition under a seven-day non-aggression pact. Start with `/coalition create`.\n🌍 **Countries** — claim from all 195 countries, receive passive GDP automatically, permanently grow it with `/country develop`, or conquer developed territory using your vehicles. Browse `/country atlas`.\n🛡️ **Attack log** — `/attacklog` publicly posts successful attacks and robberies against you.\n🏷️ **Titles** — buy vanity titles in `/titleshop`, wear your collection with `/titles`, and check achievements with `/titles view:earned`. Retired titles stay wearable by existing owners.",
        "commands": "`/casino` `/casinostats` `/daily` `/jobs` `/job` `/work` `/trivia` `/triviastats` `/event` `/balance` `/slots` `/blackjack`\n`/roulette` `/wheel` `/bank` `/deposit`\n`/withdraw` `/robbank` `/steal` `/hex` `/attacklog` `/icbm` `/aa` `/vehicle` `/thor` `/isd` `/exploration` `/continuity` `/warfare` `/deepvault` `/arsenal` `/coalition` `/copcall` `/getaway`\n`/vaultupgrade` `/shop` `/buy` `/titleshop` `/buytitle` `/give`\n`/leaderboard` `/season` `/badges` `/titles` `/plushies` `/loan` `/repay` `/country`\n`/collect` `/loans` `/ledger` `/publicledger`",
    },
    "Configuration": {
        "emoji": "⚙",
        "blurb": "Approved staff roles can manage ordinary server settings here. Economy settings and the audit log destination remain owner-only. The owner can set `general.log_channel` and `economy.channel`; staff can adjust `general.embed_color` and protect important channel IDs with `moderation.protected_channels`. Try `/config view` to see available settings. The bot owner can privately draft and approve public update summaries with `/patchnotes`; approval confirms the exact preview is public and already deployed.",
        "commands": "`/config view` `/config set` `/config reset`\n`/config export` `/config import`\nOwner: `/patchnotes draft` `/patchnotes review` `/patchnotes queue` `/patchnotes cancel`",
    },
    "About Szofie": {
        "emoji": "ℹ️",
        "blurb": f"Szofie v{__version__} combines economy, games, countries and community tools. Runtime: discord.py {discord.__version__} · Python {platform.python_version()}.",
        "commands": "`/help` `/ping`",
    },
}


class General(commands.Cog):
    """Help and diagnostics."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.started_at = time.time()

    @app_commands.command(name="help", description="Everything Szofie can do.")
    @app_commands.describe(category="Jump straight to one section")
    @app_commands.choices(category=[app_commands.Choice(name=name, value=name) for name in CATEGORIES])
    async def help_cmd(
        self, interaction: discord.Interaction, category: Optional[app_commands.Choice[str]] = None
    ) -> None:
        cfg = self.bot.config.for_guild(interaction.guild_id) if interaction.guild_id else None
        color = cfg.color if cfg else 11894492
        if category:
            block = CATEGORIES[category.value]
            embed = ui.base_embed(
                title=f"{block['emoji']} {category.value}", description=block["blurb"], color=color
            )
            embed.add_field(name="Commands", value=block["commands"], inline=False)
            await ui.respond(interaction, embed=embed, ephemeral=False)
            return
        description = "A multirole bot for this server. Every command is a slash command — type `/` and start typing to find one.\nUse `/help category:<section>` for detail; the Configuration section includes quick setup."
        collected: List[Tuple[str, str]] = []
        for name, block in CATEGORIES.items():
            combined = f"{block['blurb']}\n{block['commands']}"
            chunks = []
            remaining = combined
            while remaining:
                if len(remaining) <= 1024:
                    chunks.append(remaining)
                    break
                cut = remaining.rfind("\n", 0, 1025)
                if cut < 1:
                    cut = remaining.rfind(" ", 0, 1025)
                if cut < 1:
                    cut = 1024
                chunks.append(remaining[:cut].rstrip())
                remaining = remaining[cut:].lstrip()
            for index, chunk in enumerate(chunks):
                field_name = f"{block['emoji']} {name}" if index == 0 else "\u200b"
                collected.append((field_name, chunk))
        pages: List[discord.Embed] = []
        page = ui.base_embed(title="Szofie", description=description, color=color)
        for field_name, value in collected:
            added = len(field_name) + len(value)
            if page.fields and (len(page) + added > 5600 or len(page.fields) >= 20):
                pages.append(page)
                page = ui.base_embed(title="Szofie — more commands", color=color)
            page.add_field(name=field_name, value=value, inline=False)
        pages.append(page)
        for index, help_page in enumerate(pages, 1):
            help_page.set_footer(text=f"Szofie v{__version__} · Page {index}/{len(pages)}")
        if len(pages) > 1:
            view = ui.Paginator(pages, interaction.user.id)
            await ui.respond(interaction, embed=pages[0], view=view, ephemeral=False)
        else:
            await ui.respond(interaction, embed=pages[0], ephemeral=False)

    @app_commands.command(name="ping", description="Check that Szofie is alive and how fast it is.")
    async def ping(self, interaction: discord.Interaction) -> None:
        latency = self.bot.latency * 1000
        cfg = self.bot.config.for_guild(interaction.guild_id) if interaction.guild_id else None
        embed = ui.base_embed(title="Pong", color=cfg.color if cfg else 11894492)
        embed.add_field(name="Gateway", value=f"`{latency:.0f} ms`", inline=True)
        uptime = time.time() - self.started_at
        embed.add_field(name="Uptime", value=f"`{ui.human_duration(uptime)}`", inline=True)
        await ui.respond(interaction, embed=embed, ephemeral=True)

    @commands.command(name="sync")
    async def sync(self, ctx: commands.Context, scope: Optional[str] = None) -> None:
        """Re-register slash commands. Usage: !sync  |  !sync global"""
        if ctx.author.id not in self.bot.owner_ids_set:
            return
        if scope == "global":
            synced = await self.bot.tree.sync()
            await ctx.reply(f"Synced {len(synced)} command(s) globally. Can take up to an hour to show.")
            return
        if ctx.guild is None:
            await ctx.reply("Run this in a server, or use `!sync global`.")
            return
        self.bot.tree.copy_global_to(guild=ctx.guild)
        synced = await self.bot.tree.sync(guild=ctx.guild)
        await ctx.reply(f"Synced {len(synced)} command(s) to **{ctx.guild.name}**.")

    @commands.command(name="reload")
    async def reload(self, ctx: commands.Context, cog: str) -> None:
        """Hot-reload one cog without restarting. Usage: !reload moderation"""
        if ctx.author.id not in self.bot.owner_ids_set:
            return
        name = cog if cog.startswith("cogs.") else f"cogs.{cog}"
        try:
            await self.bot.reload_extension(name)
        except commands.ExtensionError as exc:
            await ctx.reply(f"Reload failed: `{exc}`")
            return
        await ctx.reply(f"Reloaded `{name}`.")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(General(bot))
