"""Requester-bound boards and fresh confirmations for existing command flows."""

import discord
from . import ui


class BoundView(discord.ui.View):
    def __init__(self, uid, timeout=300):
        super().__init__(timeout=timeout)
        self.uid, self.message = (int(uid), None)

    async def interaction_check(self, interaction):
        if interaction.user.id == self.uid:
            return True
        await ui.respond(
            interaction, content="Open your own activity screen to use these controls.", ephemeral=True
        )
        return False

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


class Confirm(BoundView):
    def __init__(self, uid, label, callback):
        super().__init__(uid, 120)
        self.callback, self.used = (callback, False)
        button = discord.ui.Button(label=label[:80], style=discord.ButtonStyle.success)
        button.callback = self.commit
        self.add_item(button)

    async def commit(self, interaction):
        if not await self.interaction_check(interaction):
            return
        if self.used:
            await ui.respond(interaction, content="This confirmation was already used.", ephemeral=True)
            return
        self.used = True
        for item in self.children:
            item.disabled = True
        await ui.defer_response(interaction, ephemeral=True)
        await self.callback(interaction)


class Entry(BoundView):
    def __init__(self, cog, uid, kind):
        super().__init__(uid)
        self.cog, self.kind = (cog, kind)
        button = discord.ui.Button(
            label="Contracts & research" if kind == "fish" else "Civilian contracts",
            style=discord.ButtonStyle.primary,
        )
        button.callback = self.open
        self.add_item(button)

    async def open(self, interaction):
        if await self.interaction_check(interaction):
            await self.cog.activity_board(interaction)


class Board(BoundView):
    def __init__(self, cog, uid, options, token):
        super().__init__(uid)
        self.cog, self.token = (cog, token)
        if options:
            select = discord.ui.Select(
                placeholder="Select a contract or research project",
                options=[discord.SelectOption(label=label[:100], value=value) for label, value in options][
                    :25
                ],
            )
            select.callback = self.select
            self.select_control = select
            self.add_item(select)
        button = discord.ui.Button(label="Refresh board", style=discord.ButtonStyle.secondary)
        button.callback = self.refresh
        self.add_item(button)

    async def select(self, interaction):
        if await self.interaction_check(interaction):
            await self.cog.activity_preview(interaction, self.token, self.select_control.values[0])

    async def refresh(self, interaction):
        if await self.interaction_check(interaction):
            await self.cog.activity_board(interaction)


async def show(interaction, embed, view):
    await ui.respond(interaction, embed=embed, view=view, ephemeral=True)
    view.message = await ui.response_message(interaction)
