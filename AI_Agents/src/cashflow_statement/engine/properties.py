"""Stage 4: build goal-property outcomes (FV, mortgage)."""
from __future__ import annotations
from datetime import date

from cashflow_statement.models import GoalProperty
from cashflow_statement.engine._types import RunContext, GoalPropertyOutcome
from cashflow_statement.engine.dates import _round_thousand, fy_years_between
from cashflow_statement.engine.mortgages import build_goal_property_mortgage
from financial_primitives.inflation import inflate


def build_goal_properties(
    properties: list[GoalProperty],
    ctx: RunContext,
    horizon_end: date,
    warnings: list[str],
) -> list[GoalPropertyOutcome]:
    outcomes: list[GoalPropertyOutcome] = []
    for p in properties:
        # Past-date check handled at engine entry (validate_input_only) — no guard needed here.
        # Integer FY-year diff convention.
        years_to_goal = fy_years_between(ctx.latest_update_date, p.goal_date)
        inflation = p.inflation_annual if p.inflation_annual is not None else ctx.inflation_property

        # Target FV (spec calc #7)
        if p.target_fv is not None:
            target_fv = _round_thousand(p.target_fv)
        else:
            target_fv = _round_thousand(inflate(p.target_pv, inflation, years_to_goal))

        # Source PV — given target_pv if provided, otherwise reverse-derive from FV.
        # Goal_value_pv is unrounded by design; target_fv is the rounded anchor.
        # When reverse-deriving, the result may drift by up to ~₹500 from a "true" PV —
        # informational/display field only, not a cashflow driver.
        if p.target_pv is not None:
            goal_value_pv = float(p.target_pv)
        else:
            goal_value_pv = float(target_fv) / ((1 + inflation) ** years_to_goal)

        if not p.is_downpayment_only:
            outcomes.append(GoalPropertyOutcome(
                name=p.name, target_fv=target_fv, corpus_required_fv=target_fv,
                mortgage_amount=0, amortization=None,
                goal_date=p.goal_date, goal_value_pv=goal_value_pv,
                inflation_used=inflation,
            ))
            continue

        # Mortgage path: resolve downpayment.
        # `downpayment_pct` (fraction of target_fv) and `upfront_amount` (PV ₹ inflated to FV)
        # are XOR-validated in the model. One of them is non-None here.
        if p.downpayment_pct is not None:
            upfront_fv = _round_thousand(target_fv * p.downpayment_pct)
        else:
            upfront_fv = _round_thousand(inflate(p.upfront_amount, inflation, years_to_goal))

        mortgage_amount = max(target_fv - upfront_fv, 0)

        # Mortgage tenure + interest fall back to ctx defaults if user didn't override.
        tenure_years = (
            p.mortgage_tenure_years
            if p.mortgage_tenure_years is not None
            else ctx.default_mortgage_tenure_years
        )
        interest_annual = (
            p.mortgage_interest_annual
            if p.mortgage_interest_annual is not None
            else ctx.default_mortgage_interest_annual
        )

        # Simple monthly rate `annual/12` everywhere — matches Indian banking
        # "monthly reducing balance" convention. (Excel B82 used compound; we diverge.)
        n_months = tenure_years * 12
        monthly_rate = interest_annual / 12

        amortization = build_goal_property_mortgage(
            property_ref=f"goal:{p.name}",
            start_date=p.goal_date,
            principal=mortgage_amount,
            monthly_rate=monthly_rate,
            tenure_months=n_months,
            horizon_end=horizon_end,
        )

        outcomes.append(GoalPropertyOutcome(
            name=p.name,
            target_fv=target_fv,
            corpus_required_fv=upfront_fv,
            mortgage_amount=mortgage_amount,
            amortization=amortization,
            goal_date=p.goal_date,
            goal_value_pv=goal_value_pv,
            inflation_used=inflation,
        ))
    return outcomes
