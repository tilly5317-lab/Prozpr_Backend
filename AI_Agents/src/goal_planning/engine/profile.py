"""Stage 1: build initial RunContext from profile + assumptions."""
from __future__ import annotations
from goal_planning.models import Assumptions, ClientProfile
from goal_planning.engine._types import RunContext
from goal_planning.engine.dates import (
    fy_for_date, fy_end_after, near_term_cutoff, medium_term_cutoff, real_roi_monthly,
)


# B30 default: IFNA(B28, B29) where B29=0.8; spec uses 0.75 as combined default
DEFAULT_SIP_SHARE = 0.75


def build_initial_context(profile: ClientProfile, assumptions: Assumptions) -> RunContext:
    """Stage 1 of pipeline: resolve profile + assumptions into a RunContext."""
    nfa = profile.financial_assets - profile.financial_liabilities_excl_mortgage
    annual_household_expense = profile.monthly_household_expense * 12

    current_fy_year = fy_for_date(profile.latest_update_date)
    current_fy_end = fy_end_after(profile.latest_update_date)
    near_term_end = near_term_cutoff(profile.latest_update_date, assumptions.near_term_horizon_years)
    medium_term_end = medium_term_cutoff(near_term_end, assumptions.medium_term_horizon_years)

    real_monthly = real_roi_monthly(
        roi_nominal=assumptions.roi_retired_portfolio_annual,
        inflation=assumptions.inflation_household_expense,
    )

    return RunContext(
        nfa=nfa,
        latest_update_date=profile.latest_update_date,
        annual_income=profile.annual_income,
        annual_household_expense=annual_household_expense,
        monthly_household_expense=profile.monthly_household_expense,
        monthly_investment_next_12m=profile.monthly_investment_next_12m,
        tax_rate=profile.tax_rate,
        current_fy_end=current_fy_end,
        current_fy_year=current_fy_year,
        near_term_end=near_term_end,
        medium_term_end=medium_term_end,
        retirement_date_considered=None,
        retired_portfolio_roi_annual=assumptions.roi_retired_portfolio_annual,
        real_roi_retired_monthly=real_monthly,
        sip_share=DEFAULT_SIP_SHARE,
        annual_income_growth=assumptions.annual_income_growth,
        annual_invested_amount_growth=assumptions.annual_invested_amount_growth,
        inflation_household_expense=assumptions.inflation_household_expense,
        near_term_roi=assumptions.roi_near_term_post_tax,
        mid_term_roi=assumptions.roi_mid_term_post_tax,
        long_term_roi=assumptions.roi_long_term_post_tax,
    )
