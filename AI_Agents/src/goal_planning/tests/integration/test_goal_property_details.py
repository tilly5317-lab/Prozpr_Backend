from datetime import date
from goal_planning.models import (
    GoalPlanningInput, ClientProfile, RetirementInput, GoalProperty,
)
from goal_planning.engine import compute_full_projection


def _baseline():
    return GoalPlanningInput(
        profile=ClientProfile(
            latest_update_date=date(2026, 5, 9), annual_income=2_000_000, tax_rate=0.30,
            financial_assets=20_000_000, financial_liabilities_excl_mortgage=0,
            monthly_household_expense=80_000,
        ),
        retirement=RetirementInput(date_of_birth=date(1976, 5, 9)),
    )


def test_property_details_cash_purchase():
    inp = _baseline().model_copy(update={
        "goal_properties": [GoalProperty(name="house", target_pv=10_000_000, goal_date=date(2030, 5, 9))],
    })
    out = compute_full_projection(inp)
    assert len(out.goal_property_details) == 1
    d = out.goal_property_details[0]
    assert d.name == "house"
    assert d.is_downpayment_only is False
    assert d.mortgage_amount == 0
    assert d.mortgage_emi_monthly is None
    assert d.target_pv == 10_000_000
    assert d.target_fv > d.target_pv  # inflated


def test_property_details_with_mortgage():
    inp = _baseline().model_copy(update={
        "goal_properties": [GoalProperty(
            name="house2", target_pv=10_000_000,
            is_downpayment_only=True, upfront_amount=2_000_000,
            goal_date=date(2030, 5, 9),
            mortgage_tenure_years=20, mortgage_interest_annual=0.085,
        )],
    })
    out = compute_full_projection(inp)
    d = out.goal_property_details[0]
    assert d.is_downpayment_only is True
    assert d.upfront_amount == 2_000_000
    assert d.mortgage_amount > 0
    assert d.mortgage_emi_monthly is not None and d.mortgage_emi_monthly > 0
    assert d.mortgage_total_interest is not None and d.mortgage_total_interest > 0
    assert d.mortgage_payoff_date is not None
