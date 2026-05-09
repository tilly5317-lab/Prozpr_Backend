from datetime import date
import pytest
from goal_planning.models import Assumptions, ClientProfile, GoalProperty
from goal_planning.engine.profile import build_initial_context
from goal_planning.engine.properties import build_goal_properties


def _ctx():
    return build_initial_context(
        ClientProfile(
            latest_update_date=date(2026, 5, 9), annual_income=2_000_000, tax_rate=0.30,
            financial_assets=20_000_000, financial_liabilities_excl_mortgage=5_000_000,
            monthly_household_expense=80_000,
        ),
        Assumptions(),
    )


def test_cash_purchase_no_mortgage():
    props = [GoalProperty(name="house_1", target_pv=10_000_000, goal_date=date(2030, 5, 9))]
    outcomes = build_goal_properties(props, _ctx(), [])
    assert len(outcomes) == 1
    o = outcomes[0]
    assert o.mortgage_amount == 0
    assert o.amortization is None
    # FV at goal date ~= 10M x 1.06^4 ~= 12,624,770 -> rounded
    assert 12_500_000 <= o.payout_amount_fv <= 12_700_000
    assert o.payout_amount_fv == o.target_fv


def test_mortgage_path_payout_is_upfront_only():
    props = [GoalProperty(
        name="house_2", target_pv=10_000_000, is_downpayment_only=True,
        upfront_amount=2_000_000, goal_date=date(2030, 5, 9),
        mortgage_tenure_years=20, mortgage_interest_annual=0.085,
    )]
    outcomes = build_goal_properties(props, _ctx(), [])
    o = outcomes[0]
    assert o.mortgage_amount > 9_000_000
    assert o.mortgage_amount < 11_000_000
    # Payout == upfront_FV, NOT full target_FV
    assert o.payout_amount_fv < 3_000_000
    assert o.payout_amount_fv < o.target_fv
    assert o.amortization is not None
    assert o.amortization.property_ref == "goal:house_2"


def test_target_fv_provided_skips_inflation():
    props = [GoalProperty(name="house_3", target_fv=15_000_000, goal_date=date(2030, 5, 9))]
    outcomes = build_goal_properties(props, _ctx(), [])
    o = outcomes[0]
    # When target_fv given directly, no inflation applied
    assert 14_900_000 <= o.target_fv <= 15_100_000
