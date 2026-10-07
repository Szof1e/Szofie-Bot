"""Pure, exact-integer presentation helpers for donut amounts.

Only the display is rounded. Balances, transactions and input parsing retain
their exact integer values. Below a trillion, ordinary comma grouping remains.
Named short-scale units extend through trigintillion (10**93).
"""

from __future__ import annotations

_LARGE_UNITS = (
    "trillion",
    "quadrillion",
    "quintillion",
    "sextillion",
    "septillion",
    "octillion",
    "nonillion",
    "decillion",
    "undecillion",
    "duodecillion",
    "tredecillion",
    "quattuordecillion",
    "quindecillion",
    "sexdecillion",
    "septendecillion",
    "octodecillion",
    "novemdecillion",
    "vigintillion",
    "unvigintillion",
    "duovigintillion",
    "tresvigintillion",
    "quattuorvigintillion",
    "quinquavigintillion",
    "sesvigintillion",
    "septemvigintillion",
    "octovigintillion",
    "novemvigintillion",
    "trigintillion",
)


def format_donuts(value: int, *, signed: bool = False) -> str:
    """Readable short-scale words from 10**12 to 10**93, with two decimals max.

    Integer arithmetic avoids overflow and float precision loss. Rounding up
    to 1,000 of a unit promotes the display to the next unit. Beyond the named
    range, scientific notation keeps arbitrarily large balances readable.
    """
    number = int(value)
    magnitude = abs(number)
    if magnitude < 10**12:
        return f"{number:+,}" if signed else f"{number:,}"
    sign = "-" if number < 0 else "+" if signed else ""
    index, scale = (0, 10**12)
    while index + 1 < len(_LARGE_UNITS) and magnitude >= scale * 1000:
        index += 1
        scale *= 1000
    hundredths = (magnitude * 100 + scale // 2) // scale
    if hundredths >= 100000:
        index += 1
        scale *= 1000
        hundredths = (magnitude * 100 + scale // 2) // scale
    if index >= len(_LARGE_UNITS):
        exponent = len(str(magnitude)) - 1
        scale = 10**exponent
        hundredths = (magnitude * 100 + scale // 2) // scale
        if hundredths >= 1000:
            exponent += 1
            hundredths = 100
        unit = f"× 10^{exponent}"
    else:
        unit = _LARGE_UNITS[index]
    whole, fraction = divmod(hundredths, 100)
    digits = str(whole)
    if fraction:
        digits += "." + f"{fraction:02d}".rstrip("0")
    return f"{sign}{digits} {unit}"


def format_purchase_shortfall(cost: int, available: int) -> str:
    """Explain a failed wallet/bank purchase without inspecting other holdings."""

    def amount(value: int) -> str:
        return f"{format_donuts(value)} {('donut' if value == 1 else 'donuts')}"

    return f"Not enough donuts.\nRequired: **{amount(cost)}**\nAvailable (wallet + bank): **{amount(available)}**\nShortfall: **{amount(max(0, cost - available))}**"
