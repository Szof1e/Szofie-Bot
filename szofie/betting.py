"""Shared parsing for every player-entered donut amount.

The pure parser understands K/M/B/T/Q/QA/QI/SX suffixes and the half/all family.
Transformers then resolve those keywords against the correct live balance pool.
"""

from __future__ import annotations
import re
from typing import Any, Optional
import discord
from discord import app_commands
from .checks import Denied
from . import ui

SUFFIXES = {
    "": 1,
    "k": 1000,
    "m": 1000000,
    "b": 1000000000,
    "t": 1000000000000,
    "q": 1000000000000000,
    "qa": 1000000000000000,
    "qi": 1000000000000000000,
    "sx": 1000000000000000000000,
}
ALL_WORDS = {"all", "full", "max", "allin", "all-in", "everything"}
HALF_WORDS = {"half", "h"}


class AmountParseError(ValueError):
    """A friendly validation failure for a player-entered donut amount."""


def parse_amount(
    value: Any, *, available: Optional[int] = None, default_all: bool = False, allow_negative: bool = False
) -> int:
    """Parse one exact donut amount without float rounding.

    ``available`` enables half/all keywords. ``default_all`` makes a blank option
    resolve to that same balance, which is used by deposit and withdrawal commands.
    """
    if value is None or not str(value).strip():
        if default_all and available is not None:
            return max(0, int(available))
        raise AmountParseError("Enter a donut amount.")
    raw = str(value).strip().lower().replace(",", "").replace("_", "").replace(" ", "")
    if raw in ALL_WORDS:
        if available is None:
            raise AmountParseError("`all` is not available for this command.")
        return max(0, int(available))
    if raw in HALF_WORDS:
        if available is None:
            raise AmountParseError("`half` is not available for this command.")
        return max(0, int(available) // 2)
    match = re.fullmatch("([+-]?)(\\d+(?:\\.\\d+)?)(qa|qi|sx|[kmbtq]?)", raw)
    if match is None:
        raise AmountParseError(
            "Use a number such as `25000`, shorthand such as `25k`/`2.5m`/`1b`/`1qi`/`1sx`, or `half` / `all`."
        )
    sign, number, suffix = match.groups()
    if sign == "-" and (not allow_negative):
        raise AmountParseError("The amount must be positive.")
    whole, separator, fraction = number.partition(".")
    numerator = int(whole + fraction) * SUFFIXES[suffix]
    denominator = 10 ** len(fraction) if separator else 1
    amount, remainder = divmod(numerator, denominator)
    if remainder:
        raise AmountParseError("That shorthand does not resolve to a whole donut.")
    if sign == "-":
        amount = -amount
    if amount == 0 or (amount < 0 and (not allow_negative)):
        raise AmountParseError("The amount must be greater than zero.")
    return amount


def _balances(interaction: discord.Interaction) -> tuple[int, int]:
    """Return this invoker's live wallet and bank balances."""
    if interaction.guild_id is None:
        return (0, 0)
    bot = interaction.client
    cfg = bot.config.for_guild(interaction.guild_id)
    starting = int(cfg.get("economy.starting_balance", 100))
    user = bot.economy.user(interaction.guild_id, interaction.user.id, starting)
    return (int(user.get("donuts", 0)), int(user.get("bank", 0)))


class BetTransformer(app_commands.Transformer):
    """Resolve a flexible amount against the player's live wallet."""

    async def transform(self, interaction: discord.Interaction, value: str) -> int:
        wallet, _ = _balances(interaction)
        try:
            return parse_amount(value, available=wallet)
        except AmountParseError as exc:
            raise Denied(str(exc))


class AcknowledgedBetTransformer(BetTransformer):
    """For handlers that support deferred replies: ack before loading balances."""

    async def transform(self, interaction: discord.Interaction, value: str) -> int:
        await ui.defer_response(interaction)
        return await super().transform(interaction, value)


class SpendTransformer(app_commands.Transformer):
    """Resolve flexible spending against the player's wallet and bank together."""

    async def transform(self, interaction: discord.Interaction, value: str) -> int:
        wallet, bank = _balances(interaction)
        try:
            return parse_amount(value, available=wallet + bank)
        except AmountParseError as exc:
            raise Denied(str(exc))


class SignedAmountTransformer(app_commands.Transformer):
    """Parse an owner/admin adjustment with optional negative shorthand."""

    async def transform(self, interaction: discord.Interaction, value: str) -> int:
        del interaction
        try:
            return parse_amount(value, allow_negative=True)
        except AmountParseError as exc:
            raise Denied(str(exc))
