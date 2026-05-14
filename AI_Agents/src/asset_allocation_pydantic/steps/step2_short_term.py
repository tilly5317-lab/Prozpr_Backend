from __future__ import annotations

from ..models import AllocationInput, FutureInvestment, Step2Output
from ..tables import MEDIUM_TERM_BOUNDARY_MONTHS, TAX_RATE_SHORT_TERM_ARBITRAGE_THRESHOLD
from ..utils import round_to_100


def run(inp: AllocationInput, remaining_corpus: int) -> Step2Output:
    goals_allocated = [
        g for g in inp.goals if g.time_to_goal_months < MEDIUM_TERM_BOUNDARY_MONTHS
    ]
    asset_subgroup = (
        "arbitrage"
        if inp.effective_tax_rate >= TAX_RATE_SHORT_TERM_ARBITRAGE_THRESHOLD
        else "debt_subgroup"
    )

    total_goal_amount = round_to_100(sum(g.amount_needed for g in goals_allocated))

    if total_goal_amount > remaining_corpus:
        allocated_amount = remaining_corpus
        negotiable = [g.goal_name for g in goals_allocated if g.goal_priority == "negotiable"]
        negotiable_str = ", ".join(negotiable) if negotiable else "none flagged"
        msg = (
            f"Your short-term goals ask for a bit more than your current corpus "
            f"alone. The remaining amount is wealth to create through your "
            f"monthly investments before these goals come due — stepping up "
            f"your SIPs (or flexing negotiable goals like {negotiable_str}) "
            f"makes each one comfortably reachable."
        )
        future_investment = FutureInvestment(
            bucket="short_term",
            future_investment_amount=total_goal_amount - remaining_corpus,
            message=msg,
        )
    else:
        allocated_amount = total_goal_amount
        future_investment = None

    new_remaining = remaining_corpus - allocated_amount

    subgroup_amounts: dict[str, int] = {asset_subgroup: allocated_amount}

    return Step2Output(
        goals_allocated=goals_allocated,
        asset_subgroup=asset_subgroup,
        total_goal_amount=total_goal_amount,
        allocated_amount=allocated_amount,
        remaining_corpus=new_remaining,
        future_investment=future_investment,
        subgroup_amounts=subgroup_amounts,
    )
