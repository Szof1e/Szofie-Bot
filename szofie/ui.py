"""Shared embed styling and interactive components."""

from __future__ import annotations
from typing import Callable, Optional
import discord
from .amounts import format_donuts, format_purchase_shortfall

OK = "✅"
BAD = "❌"
WARN = "⚠"
NOTE = "ℹ"
COLOR_OK = 5763719
COLOR_BAD = 15548997
COLOR_WARN = 16705372


def _clip(value: Optional[str], limit: int) -> Optional[str]:
    """Keep shared embeds inside Discord's hard title/description limits."""
    if value is None or len(value) <= limit:
        return value
    return value[: max(0, limit - 3)] + "..."


def base_embed(
    title: Optional[str] = None, description: Optional[str] = None, color: int = 11894492
) -> discord.Embed:
    return discord.Embed(title=_clip(title, 256), description=_clip(description, 4096), color=color)


def ok_embed(description: str, title: Optional[str] = None) -> discord.Embed:
    return discord.Embed(
        title=_clip(title, 256), description=_clip(f"{OK} {description}", 4096), color=COLOR_OK
    )


def error_embed(description: str, title: Optional[str] = None) -> discord.Embed:
    return discord.Embed(
        title=_clip(title, 256), description=_clip(f"{BAD} {description}", 4096), color=COLOR_BAD
    )


def warn_embed(description: str, title: Optional[str] = None) -> discord.Embed:
    return discord.Embed(
        title=_clip(title, 256), description=_clip(f"{WARN} {description}", 4096), color=COLOR_WARN
    )


def human_duration(seconds: Optional[float]) -> str:
    if not seconds:
        return "0s"
    seconds = int(seconds)
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def field_pages(title: str, description: str, fields, *, color: int = 11894492) -> list[discord.Embed]:
    """Lossless single-embed pages with room for footers under Discord's 6000 cap."""
    pages = []
    page = base_embed(title=title, description=description, color=color)
    for name, value, inline in fields:
        value = str(value) or "None"
        chunks = []
        while value:
            end = min(1024, len(value))
            if len(value) > end:
                newline = value.rfind("\n", 0, end)
                if newline >= 0:
                    end = newline + 1
            chunks.append(value[:end])
            value = value[end:]
        for index, chunk in enumerate(chunks):
            heading = str(name) if index == 0 else f"{name} (continued {index + 1})"
            heading = _clip(heading, 256)
            if len(page.fields) >= 20 or len(page) + len(heading) + len(chunk) > 5000:
                pages.append(page)
                page = base_embed(title=title, color=color)
            page.add_field(name=heading, value=chunk, inline=inline)
    pages.append(page)
    return pages


async def defer_response(interaction: discord.Interaction, *, ephemeral: bool = False) -> None:
    """Acknowledge before durable saves, lock waits, external calls or media uploads."""
    if not interaction.response.is_done():
        await interaction.response.defer(thinking=True, ephemeral=ephemeral)
        interaction.extras["szofie_deferred_private"] = ephemeral


def _protect_report(interaction, content, ephemeral, kwargs):
    """Prepare a current, correctly scoped report without yielding to another task."""
    if interaction.extras.get("szofie_nyx_report") is not None:
        from . import nyx

        blocked, ephemeral = nyx.report_policy(interaction, ephemeral)
        if blocked:
            for picture in (
                [kwargs.get("file")] + list(kwargs.get("files") or []) + list(kwargs.get("attachments") or [])
            ):
                if picture is not None:
                    picture.close()
            view = kwargs.get("view")
            if view is not None:
                view.stop()
            content = None
            kwargs = {"embed": nyx.censored_embed(), "allowed_mentions": discord.AllowedMentions.none()}
            ephemeral = True
        else:
            nyx.guard_report_pager(interaction, kwargs.get("view"), ephemeral)
    return (content, ephemeral, kwargs)


async def respond(interaction: discord.Interaction, content=None, *, ephemeral: bool = False, **kwargs):
    """Finish our deferred response, retaining each command's public/private policy."""
    content, ephemeral, kwargs = _protect_report(interaction, content, ephemeral, kwargs)
    if content is not None:
        kwargs["content"] = content
    if kwargs.get("file") is None:
        kwargs.pop("file", None)
    if not interaction.response.is_done():
        result = await interaction.response.send_message(ephemeral=ephemeral, **kwargs)
        from .nyx_reports import record_response

        await record_response(interaction, result, ephemeral, kwargs.get("view"))
        return result
    deferred_private = interaction.extras.get("szofie_deferred_private")
    if deferred_private is None:
        result = await interaction.followup.send(ephemeral=ephemeral, wait=True, **kwargs)
        interaction.extras["szofie_response_message"] = result
        from .nyx_reports import record_response

        await record_response(interaction, result, ephemeral, kwargs.get("view"))
        return result
    if deferred_private != ephemeral and (not interaction.extras.get("szofie_prefix")):
        interaction.extras["szofie_privacy_transition"] = True
        try:
            await interaction.edit_original_response(content="Preparing your response…")
            content, ephemeral, kwargs = _protect_report(interaction, content, ephemeral, kwargs)
            result = await interaction.followup.send(ephemeral=ephemeral, wait=True, **kwargs)
            interaction.extras["szofie_response_message"] = result
            interaction.extras.pop("szofie_deferred_private", None)
            try:
                await interaction.delete_original_response()
            except discord.HTTPException:
                pass
            from .nyx_reports import record_response

            await record_response(interaction, result, ephemeral, kwargs.get("view"))
            return result
        finally:
            interaction.extras.pop("szofie_privacy_transition", None)
    picture = kwargs.pop("file", None)
    pictures = kwargs.pop("files", None)
    if picture is not None or pictures is not None:
        kwargs["attachments"] = ([picture] if picture is not None else []) + (pictures or [])
    result = await interaction.edit_original_response(**kwargs)
    interaction.extras["szofie_response_message"] = result
    interaction.extras.pop("szofie_deferred_private", None)
    from .nyx_reports import record_response

    await record_response(interaction, result, ephemeral, kwargs.get("view"))
    return result


async def response_message(interaction: discord.Interaction):
    """Retrieve the delivered message, not a deleted privacy-transition placeholder."""
    message = interaction.extras.get("szofie_response_message")
    return message if message is not None else await interaction.original_response()


async def followup_report(interaction, content=None, *, ephemeral=False, **kwargs):
    """Deliver an additional intel report after acknowledgement, with NYX guards."""
    content, ephemeral, kwargs = _protect_report(interaction, content, ephemeral, kwargs)
    if content is not None:
        kwargs["content"] = content
    result = await interaction.followup.send(ephemeral=ephemeral, wait=True, **kwargs)
    from .nyx_reports import record_response

    await record_response(interaction, result, ephemeral, kwargs.get("view"))
    return result


class ConfirmView(discord.ui.View):
    """A two-button confirmation gate for destructive actions.

    Only the invoking user can press it, and it always resolves — either by a
    click or by timing out — so a destructive command can never fire on a stale
    interaction.
    """

    def __init__(self, author_id: int, *, timeout: float = 30.0, danger_label: str = "Confirm"):
        super().__init__(timeout=timeout)
        self.author_id = author_id
        self.value: Optional[bool] = None
        self.interaction: Optional[discord.Interaction] = None
        self.confirm.label = danger_label

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                embed=error_embed("This confirmation isn't yours."), ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.value = True
        self.interaction = interaction
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(view=self)
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.value = False
        self.interaction = interaction
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(
            embed=warn_embed("Cancelled — nothing was changed."), view=self
        )
        self.stop()


class Paginator(discord.ui.View):
    """Simple embed pager for queue / trigger / job listings."""

    def __init__(
        self,
        pages: list[discord.Embed],
        author_id: int,
        *,
        timeout: float = 120.0,
        initial_index: int = 0,
        page_guard: Optional[Callable[[int], Optional[discord.Embed]]] = None,
    ):
        super().__init__(timeout=timeout)
        self.pages = pages or [base_embed(description="Nothing to show.")]
        self.author_id = author_id
        self.page_guard = page_guard
        self.index = min(max(0, int(initial_index)), len(self.pages) - 1)
        self._sync()

    def _sync(self) -> None:
        self.prev.disabled = self.index == 0
        self.next.disabled = self.index >= len(self.pages) - 1
        self.counter.label = f"{self.index + 1}/{len(self.pages)}"

    def current_page(self):
        if getattr(self, "_nyx_revoked", False):
            from . import nyx

            return nyx.censored_embed()
        if self.page_guard is not None:
            replacement = self.page_guard(self.index)
            if replacement is not None:
                self.pages[self.index] = replacement
        return self.pages[self.index]

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                embed=error_embed("Run the command yourself to page through it."), ephemeral=True
            )
            return False
        if self.page_guard is not None:
            for index in range(len(self.pages)):
                replacement = self.page_guard(index)
                if replacement is not None:
                    self.pages[index] = replacement
        return True

    @discord.ui.button(emoji="⬅", style=discord.ButtonStyle.secondary)
    async def prev(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.index = max(0, self.index - 1)
        self._sync()
        await interaction.response.edit_message(embed=self.current_page(), view=self)

    @discord.ui.button(label="1/1", style=discord.ButtonStyle.primary, disabled=True)
    async def counter(self, interaction: discord.Interaction, button: discord.ui.Button):
        pass

    @discord.ui.button(emoji="➡", style=discord.ButtonStyle.secondary)
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.index = min(len(self.pages) - 1, self.index + 1)
        self._sync()
        await interaction.response.edit_message(embed=self.current_page(), view=self)
