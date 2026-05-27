"""Stage 4: build goal-property outcomes (FV, mortgage)."""
from __future__ import annotations
from datetime import date

from cashflow_statement.models import GoalProperty, GoalPropertyDetail
from cashflow_statement.engine._types import RunContext, GoalPropertyOutcome
from cashflow_statement.engine.dates import _round_thousand, eomonth
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
        # Day-precise convention: inflate to EOMONTH(goal_date), symmetric with the
        # PV-discount in goals_table._fund_today_pv.
        years_to_goal = (eomonth(p.goal_date, 0) - ctx.latest_update_date).days / 365
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

        if upfront_fv > target_fv:
            warnings.append(
                f"goal_property '{p.name}': upfront_amount inflates to ₹{upfront_fv:,.0f} "
                f"at goal_date, which exceeds the property's target_fv ₹{target_fv:,.0f}. "
                f"Clamping upfront to target_fv — treating this as a cash purchase, no mortgage."
            )
            upfront_fv = target_fv

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
        # "monthly reducing balance" convention.
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


def build_goal_property_details(
    outcomes: list[GoalPropertyOutcome],
    source_goal_properties: list[GoalProperty],
    ctx: RunContext,
) -> list[GoalPropertyDetail]:
    """Lift internal GoalPropertyOutcome + source GoalProperty into the public
    GoalPropertyDetail list.

    Resolves tenure/interest fallbacks from ctx defaults, and reads
    emi/payoff_date/total_interest from the schedule (computed once in
    build_goal_property_mortgage).
    """
    name_to_src = {gp.name: gp for gp in source_goal_properties}
    details: list[GoalPropertyDetail] = []
    for outcome in outcomes:
        src = name_to_src[outcome.name]
        tenure_used = (
            src.mortgage_tenure_years
            if src.mortgage_tenure_years is not None
            else ctx.default_mortgage_tenure_years
        )
        interest_used = (
            src.mortgage_interest_annual
            if src.mortgage_interest_annual is not None
            else ctx.default_mortgage_interest_annual
        )
        amort = outcome.amortization
        if amort is not None and outcome.mortgage_amount > 0:
            emi = amort.emi
            total_interest = amort.total_interest
            payoff_date = amort.payoff_date
        else:
            emi = None
            total_interest = None
            payoff_date = None
        details.append(GoalPropertyDetail(
            name=outcome.name,
            target_pv=outcome.goal_value_pv,
            target_fv=outcome.target_fv,
            corpus_required_fv=outcome.corpus_required_fv,
            is_downpayment_only=src.is_downpayment_only,
            upfront_amount=src.upfront_amount,
            downpayment_pct=src.downpayment_pct,
            mortgage_amount=outcome.mortgage_amount,
            mortgage_tenure_years=tenure_used,
            mortgage_interest_annual=interest_used,
            mortgage_emi_monthly=emi,
            mortgage_total_interest=total_interest,
            mortgage_payoff_date=payoff_date,
            goal_date=src.goal_date,
        ))
    return details
