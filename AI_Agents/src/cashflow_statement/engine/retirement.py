"""Stage 2a: compute RetirementSnapshot."""
from __future__ import annotations
from datetime import date

from cashflow_statement.models import RetirementInput, RetirementSnapshot
from cashflow_statement.engine._types import RunContext
from cashflow_statement.engine.dates import _round_thousand, eomonth
from cashflow_statement.engine.exceptions import MissingDOBError
from financial_primitives.inflation import inflate, real_rate
from financial_primitives.retirement import retirement_corpus_pv


def _add_years(d: date, n: int) -> date:
    """Stdlib substitute for relativedelta(years=n). Handles Feb 29 -> Feb 28."""
    try:
        return d.replace(year=d.year + n)
    except ValueError:
        return d.replace(year=d.year + n, day=28)


def _years_between(d1: date, d2: date) -> int:
    """Whole calendar-year diff (birthday-aware). For ages: `_years_between(DOB, today)`."""
    years = d2.year - d1.year
    if (d2.month, d2.day) < (d1.month, d1.day):
        years -= 1
    return years


def compute_retirement_snapshot(
    inp: RetirementInput,
    ctx: RunContext,
    warnings: list[str],
) -> RetirementSnapshot:
    if inp.date_of_birth is None:
        raise MissingDOBError("date_of_birth is required for retirement snapshot")

    retirement_date_computed = _add_years(inp.date_of_birth, inp.retirement_age)
    retirement_date = inp.retirement_date_override or retirement_date_computed
    years_to_retire = (retirement_date - ctx.latest_update_date).days / 365.25

    # If already retired, plan for REMAINING lifespan (assumed_lifespan_years − current_age),
    # not full retirement duration (assumed_lifespan_years − retirement_age).
    if retirement_date <= ctx.latest_update_date:
        current_age = _years_between(inp.date_of_birth, ctx.latest_update_date)
        post_retirement_years = max(inp.assumed_lifespan_years - current_age, 0)
        years_to_retire = 0.0
        warnings.append(
            f"Person is already retired as of {ctx.latest_update_date}; using drawdown branch "
            f"with {post_retirement_years} remaining years (current_age={current_age})"
        )
        if post_retirement_years == 0:
            warnings.append(
                f"Person (age {current_age}) is at/past assumed_lifespan_years={inp.assumed_lifespan_years}; "
                "planning horizon is 0 years"
            )
    else:
        post_retirement_years = inp.assumed_lifespan_years - inp.retirement_age

    # Day-precise convention: inflate to EOMONTH(retirement_date), symmetric with
    # the rest of the engine (properties.py, goals_table.py, _fund_today_pv).
    inflation_years = max(
        (eomonth(retirement_date, 0) - ctx.latest_update_date).days / 365,
        0.0,
    )

    annual_expense_fv = _round_thousand(
        inflate(ctx.annual_household_expense, ctx.inflation_household_expense, inflation_years)
    )

    real_annual = real_rate(ctx.retired_portfolio_roi_annual, ctx.inflation_household_expense)

    corpus_computed = _round_thousand(retirement_corpus_pv(
        annual_expense_fv=annual_expense_fv,
        post_retirement_years=post_retirement_years,
        real_roi_annual=real_annual,
    ))

    if inp.retirement_corpus_pv_today_override is not None:
        corpus_user_fv = _round_thousand(
            inflate(inp.retirement_corpus_pv_today_override, ctx.inflation_household_expense, inflation_years)
        )
        corpus_used = corpus_user_fv
    else:
        corpus_user_fv = None
        corpus_used = corpus_computed

    # Back-discount the used FV to today's ₹ for the public PV view.
    # If user provided a PV override, use that directly (don't re-discount an inflated value).
    if inp.retirement_corpus_pv_today_override is not None:
        corpus_pv_today = inp.retirement_corpus_pv_today_override
    elif inflation_years == 0:
        corpus_pv_today = corpus_used
    else:
        corpus_pv_today = corpus_used / (1 + ctx.inflation_household_expense) ** inflation_years

    return RetirementSnapshot(
        retirement_date_computed=retirement_date_computed,
        retirement_date=retirement_date,
        years_to_retirement=years_to_retire,
        annual_household_expense_today=ctx.annual_household_expense,
        annual_household_expense_at_retirement=annual_expense_fv,
        post_retirement_years=post_retirement_years,
        real_roi_annual=real_annual,
        corpus_required_computed=corpus_computed,
        corpus_required_user_override=corpus_user_fv,
        corpus_required_used=corpus_used,
        corpus_required_pv_today=corpus_pv_today,
    )
