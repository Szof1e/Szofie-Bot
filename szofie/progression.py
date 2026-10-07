"""Ordinary career allowances and proportional theft consequences."""

LICENCES = (
    ("Professional", 10000000000, 75000000, 10),
    ("Executive", 100000000000, 500000000, 20),
    ("Industrial", 1000000000000, 3000000000, 30),
    ("National Contractor", 10000000000000, 20000000000, 40),
    ("Orbital Enterprise", 100000000000000, 150000000000, 50),
)
HELPER_TOTAL_COSTS = (40000000000, 250000000000, 1500000000000, 10000000000000, 75000000000000)


def helper_tier(user):
    employment = user.get("employment", {})
    return max(
        0,
        min(5, int(user.get("helper_certification_tier", 0)), int(employment.get("career_licence_tier", 0))),
    )


def helper_allowance(user):
    tier = helper_tier(user)
    return LICENCES[tier - 1][2] // 4 if tier and user.get("android21_helper") else 0


def helper_quote(user):
    owned = max(0, min(5, int(user.get("employment", {}).get("career_licence_tier", 0))))
    certified = helper_tier(user)
    if not user.get("android21_helper"):
        raise ValueError("Buy Android 21 Autonomous Helper in /shop first.")
    if not owned:
        raise ValueError("Buy Professional or a higher licence with /job certify first.")
    if certified >= owned:
        raise ValueError("Your helper already matches your highest owned licence.")
    credit = HELPER_TOTAL_COSTS[certified - 1] if certified else 0
    return (owned, HELPER_TOTAL_COSTS[owned - 1] - credit)


def theft_fine(user, floor, basis_points):
    visible = max(0, int(user.get("donuts", 0))) + max(0, int(user.get("bank", 0)))
    return min(visible, max(0, int(floor), visible * max(0, int(basis_points)) // 10000))


def reversal(user, attempted, percent=75, wealth_cap_percent=10):
    visible = max(0, int(user.get("donuts", 0))) + max(0, int(user.get("bank", 0)))
    return min(
        visible, max(0, int(attempted)) * max(0, percent) // 100, visible * max(0, wealth_cap_percent) // 100
    )
