from __future__ import annotations

from typing import Literal

from ..models import AllocationInput, FutureInvestment, Goal, Step2Output
from ..tables import (
    MEDIUM_TERM_BOUNDARY_MONTHS,
    TAX_RATE_SHORT_TERM_ARBITRAGE_THRESHOLD,
)
from ..utils import round_to_100


# A.3: year-2 goals (ST2) get a lower tax threshold (12.5%) than year-0/1 (ST1, 20%).
ST2_LOWER_MONTHS_INCLUSIVE: int = 24
ST2_TAX_THRESHOLD_PCT: float = 12.5


def _route(tax_rate_pct: float, threshold_pct: float) -> Literal["short_debt", "arbitrage"]:
    return "arbitrage" if tax_rate_pct > threshold_pct else "short_debt"


def run(inp: AllocationInput, remaining_corpus: int) -> Step2Output:
    # A.1: short-term bucket is months < MEDIUM_TERM_BOUNDARY_MONTHS (36).
    # A.3: split into ST1 (months < 24) and ST2 (24 <= months < 36).
    st1_goals = [g for g in inp.goals if g.time_to_goal_months < ST2_LOWER_MONTHS_INCLUSIVE]
    st2_goals = [
        g for g in inp.goals
        if ST2_LOWER_MONTHS_INCLUSIVE <= g.time_to_goal_months < MEDIUM_TERM_BOUNDARY_MONTHS
    ]
    goals_allocated = st1_goals + st2_goals

    st1_sg = _route(inp.effective_tax_rate, TAX_RATE_SHORT_TERM_ARBITRAGE_THRESHOLD)
    st2_sg = _route(inp.effective_tax_rate, ST2_TAX_THRESHOLD_PCT)

    st1_amount = round_to_100(sum(g.amount_needed for g in st1_goals))
    st2_amount = round_to_100(sum(g.amount_needed for g in st2_goals))
    total_goal_amount = st1_amount + st2_amount

    # Allocate ST1 first against remaining_corpus, then ST2 from what's left.
    st1_allocated = min(st1_amount, remaining_corpus)
    pool_after_st1 = remaining_corpus - st1_allocated
    st2_allocated = min(st2_amount, pool_after_st1)
    allocated_amount = st1_allocated + st2_allocated
    new_remaining = remaining_corpus - allocated_amount

    # Combine subgroup amounts (may carry one or two entries depending on routing).
    subgroup_amounts: dict[str, int] = {}
    if st1_allocated > 0:
        subgroup_amounts[st1_sg] = subgroup_amounts.get(st1_sg, 0) + st1_allocated
    if st2_allocated > 0:
        subgroup_amounts[st2_sg] = subgroup_amounts.get(st2_sg, 0) + st2_allocated

    # Future investment when corpus runs out mid-bucket.
    future_investment: FutureInvestment | None = None
    if total_goal_amount > remaining_corpus:
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

    # Decide the headline asset_subgroup label.
    if st1_sg == st2_sg:
        headline_sg: Literal["short_debt", "arbitrage", "mixed"] = st1_sg
    elif st1_allocated > 0 and st2_allocated > 0:
        headline_sg = "mixed"
    elif st1_allocated > 0:
        headline_sg = st1_sg
    else:
        headline_sg = st2_sg

    return Step2Output(
        goals_allocated=goals_allocated,
        asset_subgroup=headline_sg,
        total_goal_amount=total_goal_amount,
        allocated_amount=allocated_amount,
        remaining_corpus=new_remaining,
        future_investment=future_investment,
        subgroup_amounts=subgroup_amounts,
    )
