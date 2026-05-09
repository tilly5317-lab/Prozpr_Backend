"""Stage 5: build unified goals table (retirement + properties + customs)."""
from __future__ import annotations
from datetime import date

from goal_planning.models import (
    Assumptions, CustomGoal, GoalType, RetirementSnapshot,
)
from goal_planning.engine._types import RunContext, GoalInternal, GoalPropertyOutcome
from goal_planning.engine.dates import fy_end_after, year_fraction
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

    # 1. Retirement (special-case: skip inflation lookup; amount_fv = corpus_required_used)
    retirement_date = retirement_snap.retirement_date
    rows.append(GoalInternal(
        name="retirement",
        goal_type=GoalType.retirement,
        goal_date=retirement_date,
        goal_date_fy=fy_end_after(retirement_date),
        amount_pv=(
            retirement_snap.corpus_required_user_override
            if retirement_snap.corpus_required_user_override is not None
            else retirement_snap.corpus_required_computed
        ),
        amount_fv=retirement_snap.corpus_required_used,
        inflation_rate=ctx.inflation_household_expense,
        expected_roi=expected_roi_for_goal(retirement_date, ctx),
        fund_today_pv=_fund_today_pv(
            retirement_snap.corpus_required_used,
            expected_roi_for_goal(retirement_date, ctx),
            ctx, retirement_date,
        ),
    ))

    # 2. Goal properties — payout_amount_fv flows in as amount_fv (calc #11).
    # The "real" inflation has already been applied during build_goal_properties when
    # converting target_pv -> target_fv. The inflation_rate field below is bookkeeping
    # only; we record the household-expense rate as a neutral placeholder.
    for o in goal_property_outcomes:
        roi = expected_roi_for_goal(o.goal_date, ctx)
        fund_pv = _fund_today_pv(o.payout_amount_fv, roi, ctx, o.goal_date)
        rows.append(GoalInternal(
            name=o.name,
            goal_type=GoalType.property,
            goal_date=o.goal_date,
            goal_date_fy=fy_end_after(o.goal_date),
            amount_pv=o.amount_pv,
            amount_fv=o.payout_amount_fv,
            inflation_rate=ctx.inflation_household_expense,
            expected_roi=roi,
            fund_today_pv=fund_pv,
        ))

    # 3. Custom goals
    for g in custom_goals:
        if g.goal_date <= ctx.latest_update_date:
            warnings.append(f"custom_goal:{g.name} goal_date is in the past; dropped")
            continue

        years_to = year_fraction(ctx.latest_update_date, g.goal_date)
        inflation = (
            g.inflation_rate_override
            if g.inflation_rate_override is not None
            else getattr(assumptions, _INFLATION_BY_GOAL_TYPE.get(g.goal_type, "inflation_household_expense"))
        )
        if g.amount_fv is not None:
            amount_fv = g.amount_fv
            amount_pv = g.amount_pv if g.amount_pv is not None else g.amount_fv / (1 + inflation) ** years_to
        else:
            amount_pv = g.amount_pv
            amount_fv = inflate(amount_pv, inflation, years_to)

        roi = expected_roi_for_goal(g.goal_date, ctx)
        fund_pv = _fund_today_pv(amount_fv, roi, ctx, g.goal_date)

        rows.append(GoalInternal(
            name=g.name,
            goal_type=g.goal_type,
            goal_date=g.goal_date,
            goal_date_fy=fy_end_after(g.goal_date),
            amount_pv=amount_pv,
            amount_fv=amount_fv,
            inflation_rate=inflation,
            expected_roi=roi,
            fund_today_pv=fund_pv,
        ))

    rows.sort(key=lambda r: r.goal_date)
    return rows


def _fund_today_pv(amount_fv: float, expected_roi: float, ctx: RunContext, goal_date: date) -> float:
    years_to = year_fraction(ctx.latest_update_date, goal_date)
    return amount_fv / (1 + expected_roi) ** years_to
