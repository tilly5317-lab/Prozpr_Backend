from __future__ import annotations

from typing import Literal, Optional

from ..models import (
    AllocationInput,
    FutureInvestment,
    Goal,
    GoalFunding,
    GoalFundingRow,
    Step2Output,
)
from ..tables import (
    HORIZON_BOUNDARY_MONTHS,
    SIP_REVIEW_WINDOW_MONTHS,
    TAX_RATE_SHORT_TERM_ARBITRAGE_THRESHOLD,
)
from ..utils import ceil_to_100, round_to_100


def _route(
    tax_rate_pct: float, threshold_pct: float
) -> Literal["short_debt", "arbitrage"]:
    return "arbitrage" if tax_rate_pct > threshold_pct else "short_debt"


def _future_value(goal: Goal) -> float:
    return goal.amount_needed_fv if goal.amount_needed_fv is not None else goal.amount_needed


def goal_waterfall(
    goals: list[Goal],
    short_term_holdings: Optional[float],
    monthly_sip: float,
    remaining_corpus: int,
    asset_subgroup: Literal["short_debt", "arbitrage"],
) -> GoalFunding:
    """Fund short-term goals nearest first: held short-term money, then the
    front-loaded SIP, then corpus for only what the SIP cannot reach in time.
    short_term_holdings=None means no holdings on file, so the SIP share
    assumes no corpus can be moved."""
    plan: list[tuple[Goal, float, float, float]] = []
    left = short_term_holdings or 0.0
    cum_gap = 0.0
    corpus_needed = 0.0
    for g in sorted(goals, key=lambda g: g.time_to_goal_months):
        fv = _future_value(g)
        from_holdings = min(left, fv)
        left -= from_holdings
        cum_gap += fv - from_holdings
        corpus_needed = max(corpus_needed, cum_gap - monthly_sip * g.time_to_goal_months)
        plan.append((g, fv, from_holdings, fv - from_holdings))

    held = sum(p[2] for p in plan)
    need = round_to_100(held + corpus_needed)
    allocated = min(need, remaining_corpus)
    from_corpus = max(0.0, allocated - held)
    corpus_for_sip = 0.0 if short_term_holdings is None else from_corpus

    to_goals = 0.0
    sip_need = cum_gap - corpus_for_sip
    if sip_need > 0 and monthly_sip > 0:
        rate = sip_need / SIP_REVIEW_WINDOW_MONTHS
        running = 0.0
        for g, _, _, gap in plan:
            running += gap
            if g.time_to_goal_months < SIP_REVIEW_WINDOW_MONTHS:
                rate = max(rate, (running - corpus_for_sip) / g.time_to_goal_months)
        to_goals = min(monthly_sip, ceil_to_100(rate))

    rows: list[GoalFundingRow] = []
    sip_used = 0.0
    corpus_left = from_corpus
    for g, fv, from_holdings, gap in plan:
        from_sip = min(gap, max(0.0, monthly_sip * g.time_to_goal_months - sip_used))
        sip_used += from_sip
        corpus_part = max(0.0, gap - from_sip)
        goal_from_corpus = min(corpus_part, corpus_left)
        corpus_left -= goal_from_corpus
        rows.append(
            GoalFundingRow(
                goal_name=g.goal_name,
                time_to_goal_months=g.time_to_goal_months,
                amount_needed_fv=fv,
                from_holdings=from_holdings,
                from_sip=from_sip,
                from_corpus=goal_from_corpus,
                shortfall=max(0.0, corpus_part - goal_from_corpus),
            )
        )

    return GoalFunding(
        allocated_amount=allocated,
        from_corpus=from_corpus,
        shortfall=need - allocated,
        monthly_sip=monthly_sip,
        monthly_sip_to_goals=to_goals,
        asset_subgroup=asset_subgroup,
        goals=rows,
    )


def run(inp: AllocationInput, remaining_corpus: int) -> Step2Output:
    # A.1: short-term bucket is months < HORIZON_BOUNDARY_MONTHS (24). Goals from
    # 24 months up are long-term; the whole short-term bucket routes through a
    # single tax threshold (arbitrage when tax > 20%, else short_debt).
    goals_allocated = [
        g for g in inp.goals
        if g.time_to_goal_months < HORIZON_BOUNDARY_MONTHS + inp.months_to_fy_end
    ]
    subgroup = _route(inp.effective_tax_rate, TAX_RATE_SHORT_TERM_ARBITRAGE_THRESHOLD)
    funding = goal_waterfall(
        goals_allocated, inp.short_term_holdings, inp.monthly_sip, remaining_corpus, subgroup,
    )

    total_goal_amount = round_to_100(sum(_future_value(g) for g in goals_allocated))
    allocated_amount = funding.allocated_amount
    new_remaining = remaining_corpus - allocated_amount

    subgroup_amounts: dict[str, int] = {}
    if allocated_amount > 0:
        subgroup_amounts[subgroup] = allocated_amount

    future_investment: FutureInvestment | None = None
    if funding.shortfall > 0:
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
            future_investment_amount=funding.shortfall,
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
        goal_funding=funding,
    )
