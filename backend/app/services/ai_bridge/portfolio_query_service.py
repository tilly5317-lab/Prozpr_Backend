"""AI bridge — `portfolio_query_service.py`.

Sits between FastAPI services/routers and the ``AI_Agents/src`` packages (added to `sys.path` via ``ensure_ai_agents_path``). Handles env keys, async/thread boundaries, and user-context mapping. Ideal mutual fund allocation is invoked from here using ``Ideal_asset_allocation`` inside the app layer (e.g. ``ideal_allocation_runner``) so `AI_Agents` files stay untouched.
"""


from __future__ import annotations


def generate_portfolio_query_response(user, user_question: str) -> str:
    """
    Generate a personalized, DB-backed summary for portfolio_query intent.
    """
    portfolios = list(getattr(user, "portfolios", []) or [])
    goals = list(getattr(user, "financial_goals", []) or [])
    first_name = getattr(user, "first_name", None) or "there"
    if not portfolios:
        return (
            f"**Answer**\nHi {first_name}, I could not find an active portfolio in your account yet. "
            "Please add holdings/allocation first, then I can give a precise summary.\n\n"
            "**Justification**\n"
            "- I checked your account portfolio records and there are no portfolio rows to summarize."
        )

    primary = next((p for p in portfolios if getattr(p, "is_primary", False)), portfolios[0])
    total_value = float(getattr(primary, "total_value", 0) or 0)
    total_invested = float(getattr(primary, "total_invested", 0) or 0)
    gain_pct = getattr(primary, "total_gain_percentage", None)

    allocs = list(getattr(primary, "allocations", []) or [])
    alloc_lines = []
    for a in sorted(allocs, key=lambda x: float(getattr(x, "allocation_percentage", 0) or 0), reverse=True)[:4]:
        alloc_lines.append(
            f"- {getattr(a, 'asset_class', 'Unknown')}: {float(getattr(a, 'allocation_percentage', 0) or 0):.1f}%"
        )

    top_goals = [
        getattr(g, "goal_name", None) or getattr(g, "name", None)
        for g in goals
        if getattr(g, "goal_name", None) or getattr(g, "name", None)
    ][:3]
    goals_line = ", ".join(top_goals) if top_goals else "No goals recorded yet"
    gain_text = f"{float(gain_pct):.2f}%" if gain_pct is not None else "N/A"

    return (
        f"**Answer**\n"
        f"Hi {first_name}, here is your portfolio summary for: \"{user_question}\".\n"
        f"- Current portfolio value: INR {total_value:,.2f}\n"
        f"- Total invested: INR {total_invested:,.2f}\n"
        f"- Overall gain/loss: {gain_text}\n"
        f"- Goal focus: {goals_line}\n"
        + ("\n".join(alloc_lines) if alloc_lines else "- Allocation details not available")
        + "\n\n**Justification**\n"
        "- This summary is generated from your saved profile and portfolio tables.\n"
        "- Allocation distribution is taken from your latest primary portfolio records."
    )
