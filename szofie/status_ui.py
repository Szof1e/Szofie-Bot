"""Shared, presentation-only layouts for status reports.

Callers supply already-public text. This module never resolves combat odds,
loads accounts, or changes game state.
"""

from __future__ import annotations
import datetime as dt
from typing import Callable, Optional
import discord
from . import ui

STATES = {
    "none": "⚪ Not built",
    "fabricating": "🏗️ Building",
    "building": "🏗️ Building",
    "ready": "📦 Ready for launch",
    "launching": "🚀 Launching",
    "orbit": "🛰️ In orbit",
    "assembled": "✅ Assembled",
    "operational": "🟢 Operational",
    "damaged": "🔴 Damaged",
    "repairing": "🔧 Repairing",
    "cooldown": "⏳ Cooldown",
    "grounded": "⚪ Grounded · launch required",
}


def state(value: str) -> str:
    return STATES.get(value, value.replace("_", " ").title())


def line(label: str, value) -> str:
    return f"**{label}:** {value}"


def deadline(raw) -> str:
    """Discord-local finish date plus relative time, with no guessed countdown."""
    if not raw:
        return "No timer running"
    if isinstance(raw, dt.datetime):
        due = raw
    else:
        try:
            due = dt.datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except ValueError:
            return "Pending"
    if due.tzinfo is None:
        due = due.replace(tzinfo=dt.timezone.utc)
    stamp = int(due.timestamp())
    return f"<t:{stamp}:f> · <t:{stamp}:R>"


def body_name(key: str, catalog) -> str:
    body = catalog.get(key)
    return body.name if body else str(key).replace("_", " ").title()


def component_fields(catalog, components, *, windows=None, format_deadline=deadline):
    fields = []
    for key, spec in catalog.items():
        part = components[key]
        rows = [line("Status", state(str(part.get("status", "none"))))]
        if part.get("ready_at"):
            rows.append(line("Completes", format_deadline(part["ready_at"])))
        window_id = part.get("window_id") or part.get("launch_id")
        if window_id:
            record = (windows or {}).get(window_id, {})
            rows.append(line("Window ID", f"`{window_id}`"))
            if not record or not record.get("announced", True):
                rows.append(line("Interception", "Awaiting public launch warning"))
            elif "remaining_seconds" in record:
                rows.append(
                    line(
                        "Interception",
                        f"{ui.human_duration(max(0, record['remaining_seconds']))} bot-online time remaining when checked · pauses offline",
                    )
                )
            elif record.get("resolves_at"):
                rows.append(line("Interception closes", deadline(record["resolves_at"])))
        fields.append((f"🧩 {spec.get('short', spec['name'])}", "\n".join(rows)))
    return fields


def report(
    title: str,
    topics,
    *,
    color=11894492,
    checked_at=None,
    footer="Timers are snapshots · finish dates use your local timezone",
):
    """Topic-based pages; split oversized sections losslessly within embed limits."""
    pages = []
    for topic, fields in topics:
        fields = [(name, value, False) for name, value in fields if value]
        if not fields:
            continue
        chunks = ui.field_pages(f"{title} — {topic}", "", fields, color=color)
        for index, page in enumerate(chunks):
            if len(chunks) > 1:
                page.title = f"{title} — {topic} {index + 1}/{len(chunks)}"[:256]
            page.set_footer(text=footer[:800])
            if checked_at:
                page.timestamp = checked_at
            pages.append(page)
    return pages or [ui.base_embed(title=title, description="Nothing to report.", color=color)]


class StatusPager(ui.Paginator):
    """Requester-bound arrows and topic jump menu, retaining live censor guards."""

    def __init__(self, pages, author_id, *, page_guard: Optional[Callable] = None):
        self.topic_select = None
        super().__init__(pages, author_id, timeout=180, page_guard=page_guard)
        self.topic_select = discord.ui.Select(placeholder="Jump to a status section", row=1)
        self.topic_select.callback = self.select_topic
        self.add_item(self.topic_select)
        self._sync()

    def _sync(self):
        super()._sync()
        if self.topic_select is None:
            return
        start = self.index // 25 * 25
        self.topic_select.options = [
            discord.SelectOption(
                label=(page.title or f"Page {index + 1}").rsplit(" — ", 1)[-1][:100],
                value=str(index),
                default=index == self.index,
            )
            for index, page in enumerate(self.pages[start : start + 25], start)
        ]

    async def select_topic(self, interaction):
        self.index = min(max(0, int(self.topic_select.values[0])), len(self.pages) - 1)
        self._sync()
        await interaction.response.edit_message(embed=self.current_page(), view=self)


def pager(pages, author_id, *, page_guard=None):
    return StatusPager(pages, author_id, page_guard=page_guard) if len(pages) > 1 else None
