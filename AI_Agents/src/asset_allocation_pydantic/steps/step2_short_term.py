from __future__ import annotations

from typing import Literal

from ..models import AllocationInput, FutureInvestment, Step2Output
from ..tables import (
    MEDIUM_TERM_BOUNDARY_MONTHS,
    TAX_RATE_SHORT_TERM_ARBITRAGE_THRESHOLD,
)
from ..utils import round_to_100


def _route(
    tax_rate_pct: float, threshold_pct: float
) -> Literal["short_debt", "arbitrage"]:
    return "arbitrage" if tax_rate_pct > threshold_pct else "short_debt"


def run(inp: AllocationInput, remaining_corpus: int) -> Step2Output:
    # A.1: short-term bucket is months < MEDIUM_TERM_BOUNDARY_MONTHS (24). Goals
    # from 24 months up are medium-term; the whole short-term bucket routes
    # through a single tax threshold (arbitrage when tax > 20%, else short_debt).
    goals_allocated = [
        g for g in inp.goals if g.time_to_goal_months < MEDIUM_TERM_BOUNDARY_MONTHS
    ]
    subgroup = _route(inp.effective_tax_rate, TAX_RATE_SHORT_TERM_ARBITRAGE_THRESHOLD)

    total_goal_amount = round_to_100(sum(g.amount_needed for g in goals_allocated))
    allocated_amount = min(total_goal_amount, remaining_corpus)
    new_remaining = remaining_corpus - allocated_amount

    subgroup_amounts: dict[str, int] = {}
    if allocated_amount > 0:
        subgroup_amounts[subgroup] = allocated_amount

    # Future investment when corpus runs out mid-bucket.
    future_investment: FutureInvestment | None = None
    if total_goal_amount > remaining_corpus:
        negotiable = [
            g.goal_name for g in goals_allocated if g.goal_priority == "negotiable"
        ]
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

    return Step2Output(
        goals_allocated=goals_allocated,
        asset_subgroup=subgroup,
        total_goal_amount=total_goal_amount,
        allocated_amount=allocated_amount,
        remaining_corpus=new_remaining,
        future_investment=future_investment,
        subgroup_amounts=subgroup_amounts,
    )
