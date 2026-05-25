"""Stage 5: build unified goals table (retirement + properties + customs)."""
from __future__ import annotations
from datetime import date

from cashflow_statement.models import (
    Assumptions, CustomGoal, GoalType, RetirementSnapshot,
)
from cashflow_statement.engine._types import RunContext, GoalInternal, GoalPropertyOutcome
from cashflow_statement.engine.dates import _round_thousand, eomonth, fy_end_after
from financial_primitives.inflation import inflate


_INFLATION_BY_GOAL_TYPE = {
    GoalType.property: "inflation_property",
    GoalType.child_abroad_education: "inflation_child_abroad_education",
    GoalType.child_local_education: "inflation_child_local_education",
    GoalType.child_marriage: "inflation_child_marriage",
    GoalType.custom: "inflation_household_expense",
}


def expected_roi_for_goal(goal_date: date, ctx: RunContext) -> float:
    """3-band horizon lookup: near (<=near_term_end) -> mid (<=medium_term_end) -> long."""
    if goal_date <= ctx.near_term_end:
        return ctx.near_term_roi
    if goal_date <= ctx.medium_term_end:
        return ctx.mid_term_roi
    return ctx.long_term_roi


def build_goals_table(
    retirement_snap: RetirementSnapshot,
    goal_property_outcomes: list[GoalPropertyOutcome],
    custom_goals: list[CustomGoal],
    ctx: RunContext,
    assumptions: Assumptions,
    warnings: list[str],
) -> list[GoalInternal]:
    rows: list[GoalInternal] = []

    # 1. Retirement (special-case: skip inflation lookup; corpus_required_fv = corpus_required_used)
    # Goal_value_pv is the back-discounted PV (today's ₹), not the FV.
    retirement_date = retirement_snap.retirement_date
    rows.append(GoalInternal(
        name="retirement",
        goal_type=GoalType.retirement,
        goal_date=retirement_date,
        goal_date_fy=fy_end_after(retirement_date),
        goal_value_pv=retirement_snap.corpus_required_pv_today,
        # Retirement has no mortgage — goal_value_fv equals corpus_required_fv.
        goal_value_fv=retirement_snap.corpus_required_used,
        corpus_required_fv=retirement_snap.corpus_required_used,
        inflation_rate=ctx.inflation_household_expense,
        expected_roi=expected_roi_for_goal(retirement_date, ctx),
        investment_required_pv=_fund_today_pv(
            retirement_snap.corpus_required_used,
            expected_roi_for_goal(retirement_date, ctx),
            ctx, retirement_date,
        ),
    ))

    # 2. Goal properties — corpus_required_fv flows in as corpus_required_fv.
    # Report the actual inflation that was applied (user override or
    # assumptions.inflation_property fallback), not a household-expense placeholder.
    for o in goal_property_outcomes:
        roi = expected_roi_for_goal(o.goal_date, ctx)
        fund_pv = _fund_today_pv(o.corpus_required_fv, roi, ctx, o.goal_date)
        rows.append(GoalInternal(
            name=o.name,
            goal_type=GoalType.property,
            goal_date=o.goal_date,
            goal_date_fy=fy_end_after(o.goal_date),
            goal_value_pv=o.goal_value_pv,
            # Full inflated property price (differs from corpus_required_fv when mortgaged —
            # corpus only pays the downpayment).
            goal_value_fv=o.target_fv,
            corpus_required_fv=o.corpus_required_fv,
            inflation_rate=o.inflation_used,
            expected_roi=roi,
            investment_required_pv=fund_pv,
        ))

    # 3. Custom goals
    for g in custom_goals:
        # Past-date check handled at engine entry (validate_input_only) — no guard needed here.
        # Day-precise convention: inflate to EOMONTH(goal_date), symmetric with _fund_today_pv.
        inflation_years = (eomonth(g.goal_date, 0) - ctx.latest_update_date).days / 365
        inflation = (
            g.inflation_rate_override
            if g.inflation_rate_override is not None
            else getattr(assumptions, _INFLATION_BY_GOAL_TYPE.get(g.goal_type, "inflation_household_expense"))
        )
        # Goal_value_pv is unrounded by design; goal_value_fv is the rounded anchor.
        # When reverse-deriving goal_value_pv from goal_value_fv, drift may be sub-₹500.
        # PV is informational; FV is the cashflow driver. For custom goals (no mortgage)
        # corpus_required_fv == goal_value_fv — the corpus pays the full FV.
        if g.goal_value_fv is not None:
            corpus_required_fv = g.goal_value_fv
            goal_value_pv = (
                g.goal_value_pv if g.goal_value_pv is not None
                else g.goal_value_fv / (1 + inflation) ** inflation_years
            )
        else:
            goal_value_pv = g.goal_value_pv
            corpus_required_fv = _round_thousand(inflate(goal_value_pv, inflation, inflation_years))

        roi = expected_roi_for_goal(g.goal_date, ctx)
        fund_pv = _fund_today_pv(corpus_required_fv, roi, ctx, g.goal_date)

        rows.append(GoalInternal(
            name=g.name,
            goal_type=g.goal_type,
            goal_date=g.goal_date,
            goal_date_fy=fy_end_after(g.goal_date),
            goal_value_pv=goal_value_pv,
            # Custom goals have no mortgage — goal_value_fv equals corpus_required_fv.
            goal_value_fv=corpus_required_fv,
            corpus_required_fv=corpus_required_fv,
            inflation_rate=inflation,
            expected_roi=roi,
            investment_required_pv=fund_pv,
        ))

    rows.sort(key=lambda r: r.goal_date)
    return rows


def _fund_today_pv(corpus_required_fv: float, expected_roi: float, ctx: RunContext, goal_date: date) -> float:
    # Day-precise discount using EOMONTH(goal_date) / 365 — symmetric with the
    # inflation FV in properties.py and goals_table.py above.
    years_to = (eomonth(goal_date, 0) - ctx.latest_update_date).days / 365
    return corpus_required_fv / (1 + expected_roi) ** years_to
