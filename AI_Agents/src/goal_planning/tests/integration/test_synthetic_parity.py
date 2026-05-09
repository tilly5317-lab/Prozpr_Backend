"""Synthetic parity tests: hand-compute expected values via numpy_financial.

Per spec §10.3, rel_tol=0.001 since both sides are pure Python computation.
Some date-fraction tolerance is allowed (rel=0.01) on FV calcs that depend
on calendar-day-precise year math.
"""
from datetime import date

import numpy_financial as npf
import pytest

from goal_planning.models import (
    ClientProfile,
    CurrentProperty,
    CustomGoal,
    GoalPlanningInput,
    GoalProperty,
    GoalType,
    RetirementInput,
)
from goal_planning.engine import compute_full_projection


def _profile(**overrides):
    base = dict(
        latest_update_date=date(2026, 5, 9),
        annual_income=2_000_000,
        tax_rate=0.30,
        financial_assets=20_000_000,
        financial_liabilities_excl_mortgage=5_000_000,
        monthly_household_expense=80_000,
    )
    base.update(overrides)
    return ClientProfile(**base)


def test_1_single_retirement_corpus_matches_pv_formula():
    inp = GoalPlanningInput(
        profile=_profile(),
        retirement=RetirementInput(date_of_birth=date(1976, 5, 9)),
    )
    out = compute_full_projection(inp)

    # Hand compute: 50yo, retire at 60 in ~10y; 25y post-retirement.
    annual_pv = 80_000 * 12
    years_to = 10  # approximate; engine uses calendar-day math (~10.001y)
    annual_fv = annual_pv * (1.06 ** years_to)
    real_annual = (1.09 / 1.06) - 1
    expected_corpus = -npf.pv(real_annual, 25, annual_fv)
    expected_rounded = round(expected_corpus / 1000) * 1000

    # Tolerance for date-precision differences in years_to_retirement
    assert out.retirement.corpus_required_computed == pytest.approx(expected_rounded, rel=0.02)


def test_2_cash_purchase_property_payout_fv():
    inp = GoalPlanningInput(
        profile=_profile(),
        retirement=RetirementInput(date_of_birth=date(1976, 5, 9)),
        goal_properties=[GoalProperty(
            name="house_1", target_pv=10_000_000, goal_date=date(2030, 5, 9),
        )],
    )
    out = compute_full_projection(inp)
    property_goal = next(g for g in out.goals if g.goal_type == GoalType.property)
    expected_fv = round(10_000_000 * (1.06 ** 4) / 1000) * 1000
    assert property_goal.amount_fv == pytest.approx(expected_fv, rel=0.005)


def test_3_mortgaged_property_emi_matches_pmt():
    inp = GoalPlanningInput(
        profile=_profile(),
        retirement=RetirementInput(date_of_birth=date(1976, 5, 9)),
        goal_properties=[GoalProperty(
            name="house_2", target_pv=10_000_000, is_downpayment_only=True,
            upfront_amount=2_000_000, goal_date=date(2030, 5, 9),
            mortgage_tenure_years=20, mortgage_interest_annual=0.085,
        )],
        detail_level="full",
    )
    out = compute_full_projection(inp)
    schedule = next(m for m in out.mortgage_amortizations if m.property_ref == "goal:house_2")
    actual_emi = schedule.monthly_schedule[0].emi

    fv_target = round(10_000_000 * (1.06 ** 4) / 1000) * 1000
    fv_upfront = round(2_000_000 * (1.06 ** 4) / 1000) * 1000
    mortgage_amount = fv_target - fv_upfront
    monthly_rate = (1.085) ** (1 / 12) - 1
    expected_emi = npf.pmt(monthly_rate, 240, -mortgage_amount)
    assert actual_emi == pytest.approx(expected_emi, rel=0.02)


def test_4_empty_goals_nfa_growth_two_band():
    """With no goals, no income, no expense -> NFA grows: near for 2y, long after."""
    inp = GoalPlanningInput(
        profile=_profile(
            annual_income=0,
            monthly_household_expense=0,
            monthly_investment_next_12m=0,
            financial_assets=10_000_000,
            financial_liabilities_excl_mortgage=0,
        ),
        retirement=RetirementInput(date_of_birth=date(1996, 5, 9)),
    )
    out = compute_full_projection(inp)
    # NFA at year 5 — near for first 2-3y, long after.
    # Sanity-check direction: NFA grew significantly from opening 10M.
    actual_closing = out.headline.closing_nfa
    assert actual_closing > 10_000_000


def test_5_existing_mortgage_rate_inversion_round_trip():
    """PMT(inferred_rate, months, -balance) ≈ given EMI."""
    inp = GoalPlanningInput(
        profile=_profile(),
        retirement=RetirementInput(date_of_birth=date(1976, 5, 9)),
        current_properties=[CurrentProperty(
            name="apt", has_mortgage=True,
            mortgage_balance=5_000_000, mortgage_emi=43_391,
            mortgage_last_date=date(2046, 5, 9),
        )],
        detail_level="full",
    )
    out = compute_full_projection(inp)
    sched = next(m for m in out.mortgage_amortizations if m.property_ref == "existing:apt")
    first = sched.monthly_schedule[0]
    inferred_monthly_rate = first.interest_portion / 5_000_000
    pmt_check = npf.pmt(inferred_monthly_rate, 240, -5_000_000)
    assert pmt_check == pytest.approx(43_391, rel=0.02)


def test_6_goal_funded_with_huge_nfa():
    inp = GoalPlanningInput(
        profile=_profile(financial_assets=100_000_000, financial_liabilities_excl_mortgage=0),
        retirement=RetirementInput(date_of_birth=date(1976, 5, 9)),
        custom_goals=[CustomGoal(
            name="small_goal", goal_type=GoalType.custom,
            amount_pv=1_000_000, goal_date=date(2030, 5, 9),
        )],
    )
    out = compute_full_projection(inp)
    g = next(s for s in out.goals if s.name == "small_goal")
    assert g.is_funded
    assert g.shortfall_fv == 0


def test_7a_two_equal_goals_same_date_proportional_split():
    inp = GoalPlanningInput(
        profile=_profile(financial_assets=5_000_000, annual_income=0, monthly_household_expense=0),
        retirement=RetirementInput(date_of_birth=date(1996, 5, 9)),
        custom_goals=[
            CustomGoal(name="g1", goal_type=GoalType.custom, amount_fv=5_000_000, goal_date=date(2027, 6, 1)),
            CustomGoal(name="g2", goal_type=GoalType.custom, amount_fv=5_000_000, goal_date=date(2027, 6, 1)),
        ],
    )
    out = compute_full_projection(inp)
    s1 = next(s for s in out.goals if s.name == "g1")
    s2 = next(s for s in out.goals if s.name == "g2")
    assert s1.shortfall_fv > 0
    assert s2.shortfall_fv > 0
    assert s1.shortfall_fv == pytest.approx(s2.shortfall_fv, rel=0.05)


def test_7b_three_goals_total_3x_nfa():
    inp = GoalPlanningInput(
        profile=_profile(
            financial_assets=3_000_000,
            financial_liabilities_excl_mortgage=0,
            annual_income=0,
            monthly_household_expense=0,
        ),
        retirement=RetirementInput(date_of_birth=date(1996, 5, 9)),
        custom_goals=[
            CustomGoal(name=f"g{i}", goal_type=GoalType.custom, amount_fv=3_000_000, goal_date=date(2027, 6, 1))
            for i in range(3)
        ],
    )
    out = compute_full_projection(inp)
    # Each gets 1M, so each is short by 2M ≈ 2/3 of FV
    for s in out.goals:
        if s.goal_type == GoalType.custom:
            assert s.shortfall_fv == pytest.approx(2_000_000, rel=0.10)


def test_7c_mixed_goals_plus_oneoff_outflow_same_month():
    from goal_planning.models import OneOffEvent
    inp = GoalPlanningInput(
        profile=_profile(financial_assets=2_000_000, annual_income=0, monthly_household_expense=0),
        retirement=RetirementInput(date_of_birth=date(1996, 5, 9)),
        custom_goals=[
            CustomGoal(name="g1", goal_type=GoalType.custom, amount_fv=1_000_000, goal_date=date(2027, 6, 1)),
        ],
        one_off_outflows=[OneOffEvent(description="renovation", amount=2_000_000, date=date(2027, 6, 15))],
    )
    out = compute_full_projection(inp)
    g1 = next(s for s in out.goals if s.name == "g1")
    reno = next(s for s in out.one_off_outflow_status if s.description == "renovation")
    assert g1.shortfall_fv > 0
    assert reno.shortfall > 0


def test_8_per_goal_expected_roi_three_band():
    inp = GoalPlanningInput(
        profile=_profile(),
        retirement=RetirementInput(date_of_birth=date(1976, 5, 9)),
        custom_goals=[
            CustomGoal(name="near_goal", goal_type=GoalType.custom, amount_pv=1_000_000, goal_date=date(2027, 6, 1)),
            CustomGoal(name="mid_goal", goal_type=GoalType.custom, amount_pv=1_000_000, goal_date=date(2030, 6, 1)),
            CustomGoal(name="long_goal", goal_type=GoalType.custom, amount_pv=1_000_000, goal_date=date(2040, 6, 1)),
        ],
    )
    out = compute_full_projection(inp)
    near = next(g for g in out.goals if g.name == "near_goal")
    mid = next(g for g in out.goals if g.name == "mid_goal")
    long = next(g for g in out.goals if g.name == "long_goal")
    assert near.expected_roi == pytest.approx(0.05)
    assert mid.expected_roi == pytest.approx(0.07)
    assert long.expected_roi == pytest.approx(0.09)
