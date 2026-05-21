"""Stage 1: build initial RunContext from profile + assumptions."""
from __future__ import annotations
from datetime import date

from cashflow_statement.models import Assumptions, ClientProfile
from cashflow_statement.engine._types import RunContext
from cashflow_statement.engine.dates import (
    fy_for_date, fy_end_after, near_term_cutoff, medium_term_cutoff,
)


def _current_date() -> date:
    """Indirection so tests can monkeypatch the engine's notion of 'today'."""
    return date.today()


def build_initial_context(profile: ClientProfile, assumptions: Assumptions) -> RunContext:
    """Stage 1 of pipeline: resolve profile + assumptions into a RunContext."""
    latest_update_date = _current_date()

    corpus = profile.financial_assets - profile.financial_liabilities_excl_mortgage
    annual_household_expense = profile.monthly_household_expense * 12

    current_fy_year = fy_for_date(latest_update_date)
    current_fy_end = fy_end_after(latest_update_date)
    near_term_end = near_term_cutoff(latest_update_date, assumptions.near_term_horizon_years)
    medium_term_end = medium_term_cutoff(near_term_end, assumptions.medium_term_horizon_years)

    return RunContext(
        corpus=corpus,
        latest_update_date=latest_update_date,
        annual_income=profile.annual_income,
        annual_household_expense=annual_household_expense,
        monthly_household_expense=profile.monthly_household_expense,
        starting_monthly_investment=profile.starting_monthly_investment,
        effective_tax_rate=profile.effective_tax_rate,
        current_fy_end=current_fy_end,
        current_fy_year=current_fy_year,
        near_term_end=near_term_end,
        medium_term_end=medium_term_end,
        retirement_date_considered=None,
        retired_portfolio_roi_annual=assumptions.roi_retired_portfolio_annual,
        sip_share=assumptions.default_sip_share,
        annual_income_growth=assumptions.annual_income_growth,
        annual_invested_amount_growth=assumptions.annual_invested_amount_growth,
        inflation_household_expense=assumptions.inflation_household_expense,
        inflation_property=assumptions.inflation_property,
        near_term_roi=assumptions.roi_near_term_post_tax,
        mid_term_roi=assumptions.roi_mid_term_post_tax,
        long_term_roi=assumptions.roi_long_term_post_tax,
        default_mortgage_tenure_years=assumptions.default_mortgage_tenure_years,
        default_mortgage_interest_annual=assumptions.default_mortgage_interest_annual,
    )
