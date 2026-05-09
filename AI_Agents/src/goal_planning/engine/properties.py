"""Stage 4: build goal-property outcomes (FV, mortgage, amortization)."""
from __future__ import annotations
from goal_planning.models import GoalProperty
from goal_planning.engine._types import RunContext, GoalPropertyOutcome, MortgageSchedule
from goal_planning.engine.dates import _round_thousand, year_fraction
from goal_planning.engine.mortgages import _amortize_monthly, _aggregate_annual
from financial_primitives.annuity import pmt
from financial_primitives.inflation import inflate


_DEFAULT_INFLATION_PROPERTY = 0.06  # matches Assumptions.inflation_property default


def build_goal_properties(
    properties: list[GoalProperty],
    ctx: RunContext,
    warnings: list[str],
) -> list[GoalPropertyOutcome]:
    outcomes: list[GoalPropertyOutcome] = []
    for p in properties:
        if p.goal_date <= ctx.latest_update_date:
            warnings.append(f"goal:{p.name} goal_date is in the past; dropped")
            continue

        years_to_goal = year_fraction(ctx.latest_update_date, p.goal_date)
        inflation = p.inflation_annual if p.inflation_annual is not None else _DEFAULT_INFLATION_PROPERTY

        # Target FV (spec calc #7)
        if p.target_fv is not None:
            target_fv = _round_thousand(p.target_fv)
        else:
            target_fv = _round_thousand(inflate(p.target_pv, inflation, years_to_goal))

        if not p.is_downpayment_only:
            outcomes.append(GoalPropertyOutcome(
                name=p.name, target_fv=target_fv, payout_amount_fv=target_fv,
                mortgage_amount=0, amortization=None,
            ))
            continue

        # Mortgage path
        upfront_fv = _round_thousand(inflate(p.upfront_amount, inflation, years_to_goal))
        mortgage_amount = max(target_fv - upfront_fv, 0)
        n_months = p.mortgage_tenure_years * 12
        monthly_rate = (1 + p.mortgage_interest_annual) ** (1/12) - 1
        emi = pmt(monthly_rate, n_months, mortgage_amount)

        monthly_rows = _amortize_monthly(
            start=p.goal_date, principal=mortgage_amount,
            monthly_rate=monthly_rate, emi=emi, n_months=n_months,
        )
        annual_rows = _aggregate_annual(monthly_rows)

        outcomes.append(GoalPropertyOutcome(
            name=p.name,
            target_fv=target_fv,
            payout_amount_fv=upfront_fv,
            mortgage_amount=mortgage_amount,
            amortization=MortgageSchedule(
                property_ref=f"goal:{p.name}",
                start_date=p.goal_date,
                monthly_rows=monthly_rows,
                annual_rows=annual_rows,
            ),
        ))
    return outcomes
