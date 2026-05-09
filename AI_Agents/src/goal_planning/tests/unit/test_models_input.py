from datetime import date
import pytest
from pydantic import ValidationError
from goal_planning.models import Assumptions, ClientProfile, RetirementInput


def test_assumptions_defaults():
    a = Assumptions()
    assert a.inflation_property == 0.06
    assert a.inflation_child_abroad_education == 0.08
    assert a.inflation_household_expense == 0.06
    assert a.roi_long_term_post_tax == 0.09
    assert a.default_mortgage_interest_annual == 0.075
    assert a.near_term_horizon_years == 2
    assert a.medium_term_horizon_years == 3


def test_client_profile_required_fields():
    p = ClientProfile(
        latest_update_date=date(2026, 5, 9),
        annual_income=2_000_000,
        tax_rate=0.30,
        financial_assets=20_000_000,
        financial_liabilities_excl_mortgage=5_000_000,
        monthly_household_expense=80_000,
    )
    assert p.monthly_investment_next_12m is None


def test_client_profile_monthly_investment_none_vs_zero():
    p_none = ClientProfile(
        latest_update_date=date(2026, 5, 9), annual_income=0, tax_rate=0.30,
        financial_assets=0, financial_liabilities_excl_mortgage=0,
        monthly_household_expense=0,
    )
    p_zero = p_none.model_copy(update={"monthly_investment_next_12m": 0})
    assert p_none.monthly_investment_next_12m is None
    assert p_zero.monthly_investment_next_12m == 0


def test_retirement_input_defaults():
    r = RetirementInput(date_of_birth=date(1976, 5, 9))
    assert r.retirement_age == 60
    assert r.assumed_total_age == 85
    assert r.retirement_date_override is None
    assert r.retirement_corpus_pv_override is None
