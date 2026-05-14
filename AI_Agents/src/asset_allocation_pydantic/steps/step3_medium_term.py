from __future__ import annotations

from math import floor
from typing import Literal

from ..models import (
    AllocationInput,
    FutureInvestment,
    MediumTermGoalAllocation,
    Step3Output,
)
from ..tables import (
    LONG_TERM_BOUNDARY_MONTHS,
    MEDIUM_TERM_BOUNDARY_MONTHS,
    MEDIUM_TERM_HORIZON_MAX,
    MEDIUM_TERM_HORIZON_MIN,
    MEDIUM_TERM_RISK_LOW_MAX_EXCLUSIVE,
    MEDIUM_TERM_RISK_MEDIUM_MAX,
    MEDIUM_TERM_SPLIT,
    TAX_RATE_MEDIUM_LONG_ARBITRAGE_THRESHOLD,
)
from ..utils import round_to_100


def _risk_bucket(score: float) -> Literal["Low", "Medium", "High"]:
    if score < MEDIUM_TERM_RISK_LOW_MAX_EXCLUSIVE:
        return "Low"
    if score <= MEDIUM_TERM_RISK_MEDIUM_MAX:
        return "Medium"
    return "High"


def run(inp: AllocationInput, remaining_corpus: int) -> Step3Output:
    goals_in_bucket = [
        g for g in inp.goals
        if MEDIUM_TERM_BOUNDARY_MONTHS <= g.time_to_goal_months <= LONG_TERM_BOUNDARY_MONTHS
    ]
    risk_bucket = _risk_bucket(inp.effective_risk_score)
    debt_key = (
        "arbitrage_plus_income"
        if inp.effective_tax_rate >= TAX_RATE_MEDIUM_LONG_ARBITRAGE_THRESHOLD
        else "debt_subgroup"
    )

    allocations: list[MediumTermGoalAllocation] = []
    total_equity = 0
    total_debt = 0

    for g in goals_in_bucket:
        horizon = min(
            MEDIUM_TERM_HORIZON_MAX, max(MEDIUM_TERM_HORIZON_MIN, floor(g.time_to_goal_months / 12))
        )
        eq_pct, dt_pct = MEDIUM_TERM_SPLIT[(horizon, risk_bucket)]
        eq_amt = round_to_100(g.amount_needed * eq_pct / 100)
        dt_amt = round_to_100(g.amount_needed * dt_pct / 100)
        total_equity += eq_amt
        total_debt += dt_amt
        allocations.append(
            MediumTermGoalAllocation(
                goal_name=g.goal_name,
                time_to_goal_months=g.time_to_goal_months,
                amount_needed=g.amount_needed,
                goal_priority=g.goal_priority,
                horizon_years=horizon,
                equity_pct=eq_pct,
                debt_pct=dt_pct,
                equity_amount=eq_amt,
                debt_amount=dt_amt,
            )
        )

    total_goal_amount = round_to_100(sum(g.amount_needed for g in goals_in_bucket))

    if total_goal_amount > remaining_corpus:
        # Scale down both components proportionally so they fit.
        allocated_amount = remaining_corpus
        if total_goal_amount > 0:
            scale = remaining_corpus / total_goal_amount
            total_equity = round_to_100(total_equity * scale)
            total_debt = round_to_100(total_debt * scale)
        negotiable = [g.goal_name for g in goals_in_bucket if g.goal_priority == "negotiable"]
        negotiable_str = ", ".join(negotiable) if negotiable else "none flagged"
        msg = (
            f"Your medium-term goals ask for more than what's left after the "
            f"short-term allocation. The balance is wealth your ongoing "
            f"investments will create over the next few years — this is exactly "
            f"the horizon where consistent SIPs compound into real progress. "
            f"Staying the course, raising your monthly savings, or trimming "
            f"negotiable goals ({negotiable_str}) each keep you on track."
        )
        future_investment = FutureInvestment(
            bucket="medium_term",
            future_investment_amount=total_goal_amount - remaining_corpus,
            message=msg,
        )
    else:
        allocated_amount = total_equity + total_debt
        future_investment = None

    new_remaining = remaining_corpus - allocated_amount

    subgroup_amounts: dict[str, int] = {}
    if total_equity > 0:
        subgroup_amounts["multi_asset"] = total_equity
    if total_debt > 0:
        subgroup_amounts[debt_key] = total_debt

    return Step3Output(
        risk_bucket=risk_bucket,
        asset_subgroup=debt_key,
        goals_allocated=allocations,
        total_goal_amount=total_goal_amount,
        allocated_amount=allocated_amount,
        remaining_corpus=new_remaining,
        future_investment=future_investment,
        subgroup_amounts=subgroup_amounts,
    )
