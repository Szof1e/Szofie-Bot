"""Owner-reviewed public summaries; no automatic reading of private changes."""

from __future__ import annotations
import logging
import discord
from discord import app_commands
from discord.ext import commands, tasks
from szofie import checks, public_updates, ui

log = logging.getLogger("szofie.patchnotes")


class ApprovalView(discord.ui.View):
    def __init__(self, cog, entry, reviewer_id):
        super().__init__(timeout=300)
        self.cog, self.entry, self.reviewer_id = (cog, entry, reviewer_id)
        self.expected_digest = public_updates.digest(entry)

    async def interaction_check(self, interaction):
        try:
            checks.owner_only_check(interaction)
            if interaction.user.id != self.reviewer_id:
                raise checks.Denied("Open your own `/patchnotes review` before approving.")
        except (checks.Denied, ValueError, discord.HTTPException) as exc:
            message = (
                exc.reason if isinstance(exc, checks.Denied) else "Cannot authorize this patch-note review."
            )
            await ui.respond(interaction, embed=ui.error_embed(message), ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Approve live update & publish", style=discord.ButtonStyle.success)
    async def approve(self, interaction, button):
        await ui.defer_response(interaction, ephemeral=True)
        try:
            await self.cog.authorize(interaction)
            await self.cog.outbox.approve(
                self.entry["id"], self.expected_digest, interaction.guild_id, interaction.user.id
            )
        except (checks.Denied, ValueError) as exc:
            await ui.respond(interaction, embed=ui.error_embed(str(exc)), ephemeral=True)
            return
        except OSError:
            await ui.respond(
                interaction,
                embed=ui.error_embed(
                    "Approval could not be saved. Nothing was published; review and try again."
                ),
                ephemeral=True,
            )
            return
        for child in self.children:
            child.disabled = True
        self.stop()
        try:
            await interaction.message.edit(view=self)
        except discord.HTTPException:
            pass
        await self.cog.flush()
        row = self.cog.outbox.entry(self.entry["id"])
        await ui.respond(interaction, embed=self.cog.status_embed(row), ephemeral=True)


class PatchNotes(commands.Cog, name="Public Patch Notes"):
    def __init__(self, bot):
        self.bot = bot
        self.outbox = public_updates.PublicUpdates()
        self.publisher.start()

    def cog_unload(self):
        self.publisher.cancel()

    async def authorize(self, interaction):
        checks.owner_only_check(interaction)
        checks.guild_only_check(interaction)
        channel = await self.outbox.channel(self.bot, require_permissions=False)
        if channel.guild.id != interaction.guild_id:
            raise checks.Denied("Patch-note controls are only available in the updates channel's server.")

    async def flush(self):
        try:
            await self.outbox.publish(self.bot)
        except (discord.HTTPException, OSError, ValueError):
            log.warning("Public patch-note delivery deferred; queue retained.")

    @tasks.loop(seconds=60)
    async def publisher(self):
        await self.flush()

    @publisher.before_loop
    async def before_publisher(self):
        await self.bot.wait_until_ready()
        try:
            await self.outbox.channel(self.bot)
        except (discord.HTTPException, ValueError):
            log.warning(
                "Public patch-note channel unavailable or missing required permissions; posting remains queued."
            )
        else:
            log.info("Public patch-note publisher ready; only approved live summaries are eligible.")

    group = app_commands.Group(
        name="patchnotes",
        description="Privately review and approve public update notes.",
        guild_only=True,
        default_permissions=discord.Permissions(administrator=True),
    )

    @staticmethod
    def status_embed(row):
        state = row["status"]
        message = f"`{row['id']}` · **{state.title()}**"
        if state == "posted":
            message += f"\nhttps://discord.com/channels/{row['guild_id']}/{public_updates.CHANNEL_ID}/{row['message_id']}"
        elif state == "draft":
            message += (
                "\nNothing has been published. Open `/patchnotes review` to approve the exact public text."
            )
        else:
            message += (
                "\nPending delivery is retained across restarts; uncertain sends are not repeated blindly."
            )
        if row.get("last_error"):
            message += "\n" + row["last_error"]
        return ui.base_embed(title="Patch-note queue", description=message)

    @group.command(name="draft", description="Save a public-only summary privately; nothing is posted yet.")
    @app_commands.check(checks.owner_only_check)
    @app_commands.describe(
        title="Short public heading", notes="Exact public notes, within the 2,000-character post limit"
    )
    async def draft(self, interaction: discord.Interaction, title: str, notes: str):
        await ui.defer_response(interaction, ephemeral=True)
        await self.authorize(interaction)
        try:
            row = await self.outbox.draft(title, notes, interaction.guild_id, interaction.user.id)
        except ValueError as exc:
            await ui.respond(interaction, embed=ui.error_embed(str(exc)), ephemeral=True)
            return
        except OSError:
            await ui.respond(
                interaction,
                embed=ui.error_embed("The draft could not be saved. Nothing was published; try again."),
                ephemeral=True,
            )
            return
        await ui.respond(interaction, embed=self.status_embed(row), ephemeral=True)

    @group.command(
        name="review", description="Privately preview a note and confirm it is public and deployed."
    )
    @app_commands.check(checks.owner_only_check)
    async def review(self, interaction: discord.Interaction, update_id: str):
        await ui.defer_response(interaction, ephemeral=True)
        await self.authorize(interaction)
        try:
            row = self.outbox.entry(update_id)
            if row.get("guild_id") != interaction.guild_id:
                raise ValueError("That note does not belong to this server.")
            text = public_updates.validate(row)
        except ValueError as exc:
            await ui.respond(interaction, embed=ui.error_embed(str(exc)), ephemeral=True)
            return
        embed = ui.base_embed(title="Private patch-note preview", description=text)
        embed.set_footer(
            text="Approval confirms this exact text is PUBLIC and the change is already LIVE. No other changes are included."
        )
        kwargs = {"embed": embed, "ephemeral": True, "allowed_mentions": discord.AllowedMentions.none()}
        if row["status"] == "draft":
            kwargs["view"] = ApprovalView(self, row, interaction.user.id)
        else:
            embed.add_field(name="Queue status", value=self.status_embed(row).description, inline=False)
        await ui.respond(interaction, **kwargs)

    @group.command(name="queue", description="Privately list draft, pending and recently published notes.")
    @app_commands.check(checks.owner_only_check)
    async def queue(self, interaction: discord.Interaction):
        await ui.defer_response(interaction, ephemeral=True)
        await self.authorize(interaction)
        entries = self.outbox.entries(interaction.guild_id)
        rows = [row for row in entries if row["status"] not in {"posted", "cancelled"}]
        rows += [row for row in entries if row["status"] in {"posted", "cancelled"}][-10:]
        pages = [
            ui.base_embed(
                title="Private patch-note queue",
                description="\n".join(
                    (f"`{row['id']}` · {row['status']} · {row['title']}" for row in rows[index : index + 10])
                ),
            )
            for index in range(0, len(rows), 10)
        ]
        pages = pages or [
            ui.base_embed(
                title="Private patch-note queue", description="No notes queued. Use `/patchnotes draft`."
            )
        ]
        kwargs = {"embed": pages[0], "ephemeral": True}
        if len(pages) > 1:
            kwargs["view"] = ui.Paginator(pages, interaction.user.id)
        await ui.respond(interaction, **kwargs)

    @group.command(
        name="cancel", description="Cancel pending publication without deleting any posted message."
    )
    @app_commands.check(checks.owner_only_check)
    async def cancel(self, interaction: discord.Interaction, update_id: str):
        await ui.defer_response(interaction, ephemeral=True)
        await self.authorize(interaction)
        try:
            await self.outbox.cancel(update_id.upper(), interaction.guild_id)
        except ValueError as exc:
            await ui.respond(interaction, embed=ui.error_embed(str(exc)), ephemeral=True)
            return
        except OSError:
            await ui.respond(
                interaction,
                embed=ui.error_embed("Cancellation could not be saved. Check the queue and try again."),
                ephemeral=True,
            )
            return
        await ui.respond(
            interaction,
            embed=ui.ok_embed("Pending publication cancelled. Existing channel messages were not changed."),
            ephemeral=True,
        )


async def setup(bot):
    await bot.add_cog(PatchNotes(bot))
