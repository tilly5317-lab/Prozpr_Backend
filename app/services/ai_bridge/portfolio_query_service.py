"""Build a DB-backed portfolio summary for the portfolio_query intent."""

from __future__ import annotations


def generate_portfolio_query_response(user, user_question: str) -> str:
    """Return a markdown summary of the user's primary portfolio and goals."""
    portfolios = list(getattr(user, "portfolios", []) or [])
    goals = list(getattr(user, "financial_goals", []) or [])
    first_name = getattr(user, "first_name", None) or "there"

    if not portfolios:
        return (
            f"**Answer**\nHi {first_name}, I could not find an active portfolio in your account yet. "
            "Please add holdings/allocation first, then I can give a precise summary.\n\n"
            "**Justification**\n"
            "- No portfolio rows found in your account."
        )

    primary = next((p for p in portfolios if getattr(p, "is_primary", False)), portfolios[0])
    total_value = float(getattr(primary, "total_value", 0) or 0)
    total_invested = float(getattr(primary, "total_invested", 0) or 0)
    gain_pct = getattr(primary, "total_gain_percentage", None)
    gain_text = f"{float(gain_pct):.2f}%" if gain_pct is not None else "N/A"

    # Top allocation slices (by weight, descending).
    allocs = sorted(
        list(getattr(primary, "allocations", []) or []),
        key=lambda a: float(getattr(a, "allocation_percentage", 0) or 0),
        reverse=True,
    )[:4]
    alloc_lines = [
        f"- {getattr(a, 'asset_class', 'Unknown')}: "
        f"{float(getattr(a, 'allocation_percentage', 0) or 0):.1f}%"
        for a in allocs
    ]

    # First 3 goal names.
    goal_names = [
        getattr(g, "goal_name", None) or getattr(g, "name", None)
        for g in goals
        if getattr(g, "goal_name", None) or getattr(g, "name", None)
    ][:3]
    goals_line = ", ".join(goal_names) if goal_names else "No goals recorded yet"

    return (
        f"**Answer**\n"
        f"Hi {first_name}, here is your portfolio summary for: \"{user_question}\".\n"
        f"- Current portfolio value: INR {total_value:,.2f}\n"
        f"- Total invested: INR {total_invested:,.2f}\n"
        f"- Overall gain/loss: {gain_text}\n"
        f"- Goal focus: {goals_line}\n"
        + ("\n".join(alloc_lines) if alloc_lines else "- Allocation details not available")
        + "\n\n**Justification**\n"
        "- Summary generated from your saved profile and portfolio tables.\n"
        "- Allocation taken from your latest primary portfolio records."
    )
