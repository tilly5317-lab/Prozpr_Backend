"""Stage 2a: compute RetirementSnapshot."""
from __future__ import annotations
from datetime import date

from goal_planning.models import RetirementInput, RetirementSnapshot
from goal_planning.engine._types import RunContext
from goal_planning.engine.dates import _round_thousand, real_roi_monthly
from goal_planning.engine.exceptions import MissingDOBError
from financial_primitives.inflation import inflate, real_rate
from financial_primitives.retirement import retirement_corpus_pv


def _add_years(d: date, n: int) -> date:
    """Stdlib substitute for relativedelta(years=n). Handles Feb 29 -> Feb 28."""
    try:
        return d.replace(year=d.year + n)
    except ValueError:
        return d.replace(year=d.year + n, day=28)


def compute_retirement_snapshot(
    inp: RetirementInput,
    ctx: RunContext,
    warnings: list[str],
) -> RetirementSnapshot:
    if inp.date_of_birth is None:
        raise MissingDOBError("date_of_birth is required for retirement snapshot")

    retirement_date = inp.retirement_date_override or _add_years(
        inp.date_of_birth, inp.retirement_age,
    )
    years_to_retire = (retirement_date - ctx.latest_update_date).days / 365.25
    post_retirement_years = inp.assumed_total_age - inp.retirement_age

    if retirement_date <= ctx.latest_update_date:
        warnings.append(
            f"Person is already retired as of {ctx.latest_update_date}; using drawdown branch"
        )
        years_to_retire = 0.0

    annual_expense_fv = _round_thousand(
        inflate(ctx.annual_household_expense, ctx.inflation_household_expense, max(years_to_retire, 0))
    )

    real_annual = real_rate(ctx.retired_portfolio_roi_annual, ctx.inflation_household_expense)
    real_monthly = real_roi_monthly(ctx.retired_portfolio_roi_annual, ctx.inflation_household_expense)

    corpus_computed = _round_thousand(retirement_corpus_pv(
        annual_expense_fv=annual_expense_fv,
        post_retirement_years=post_retirement_years,
        real_roi_annual=real_annual,
    ))

    if inp.retirement_corpus_pv_override is not None:
        corpus_user_fv = _round_thousand(
            inflate(inp.retirement_corpus_pv_override, ctx.inflation_household_expense, max(years_to_retire, 0))
        )
        corpus_used = corpus_user_fv
    else:
        corpus_user_fv = None
        corpus_used = corpus_computed

    return RetirementSnapshot(
        retirement_date=retirement_date,
        years_to_retirement=years_to_retire,
        annual_household_expense_at_retirement=annual_expense_fv,
        post_retirement_years=post_retirement_years,
        real_roi_annual=real_annual,
        real_roi_monthly=real_monthly,
        corpus_required_computed=corpus_computed,
        corpus_required_user_override=corpus_user_fv,
        corpus_required_used=corpus_used,
    )
