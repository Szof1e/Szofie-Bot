"""Shared exact-integer wallet operations. Never use floats for currency."""

from typing import Any, Dict


def visible_total(user: Dict[str, Any]) -> int:
    return max(0, int(user.get("donuts", 0))) + max(0, int(user.get("bank", 0)))


def net_total(user: Dict[str, Any]) -> int:
    """Signed net value for ledger and existing consensual economy commands."""
    return int(user.get("donuts", 0)) + int(user.get("bank", 0))


def take_wallet_first(user: Dict[str, Any], amount: int) -> None:
    """Legacy signed-balance semantics, retained during service extraction."""
    wallet = min(amount, int(user.get("donuts", 0)))
    user["donuts"] = int(user.get("donuts", 0)) - wallet
    user["bank"] = int(user.get("bank", 0)) - (amount - wallet)


def take_visible(user: Dict[str, Any], amount: int) -> None:
    """Existing wallet-first payment semantics, shared by purchases and combat."""
    amount = max(0, int(amount))
    wallet = min(amount, max(0, int(user.get("donuts", 0))))
    user["donuts"] = max(0, int(user.get("donuts", 0))) - wallet
    user["bank"] = max(0, int(user.get("bank", 0))) - (amount - wallet)


def transfer_wallet(donor: Dict[str, Any], recipient: Dict[str, Any], amount: int) -> None:
    """Validate both ends before a synchronous, exact, non-minting transfer."""
    if type(amount) is not int or amount <= 0:
        raise ValueError("Transfer amount must be a positive whole number.")
    balance = int(donor.get("donuts", 0))
    if amount > balance:
        raise ValueError("Insufficient wallet donuts.")
    donor["donuts"] = balance - amount
    recipient["donuts"] = int(recipient.get("donuts", 0)) + amount
