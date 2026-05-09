from datetime import date
import pytest
from goal_planning.models import (
    GoalPlanningInput, ClientProfile, RetirementInput, GoalProperty, CustomGoal, GoalType,
    CurrentProperty,
)
from goal_planning.engine import compute_full_projection


def _baseline_with_goals():
    return GoalPlanningInput(
        profile=ClientProfile(
            latest_update_date=date(2026, 5, 9), annual_income=2_500_000, tax_rate=0.30,
            financial_assets=20_000_000, financial_liabilities_excl_mortgage=500_000,
            monthly_household_expense=120_000, monthly_investment_next_12m=80_000,
        ),
        retirement=RetirementInput(date_of_birth=date(1976, 5, 9)),
        custom_goals=[
            CustomGoal(name="g1", goal_type=GoalType.child_local_education,
                       amount_pv=2_500_000, goal_date=date(2035, 6, 1)),
            CustomGoal(name="g2", goal_type=GoalType.child_marriage,
                       amount_pv=3_000_000, goal_date=date(2042, 1, 1)),
        ],
    )


def test_derived_stats_has_all_fields():
    out = compute_full_projection(_baseline_with_goals())
    assert out.derived_stats is not None
    ds = out.derived_stats
    assert ds.peak_nfa_amount > 0
    assert ds.peak_nfa_date >= out.input_echo.profile.latest_update_date
    assert ds.worst_savings_fy.startswith("FY")
    assert ds.best_savings_fy.startswith("FY")
    assert isinstance(ds.goals_by_category, dict)


def test_goals_by_category_aggregates_correctly():
    out = compute_full_projection(_baseline_with_goals())
    cats = out.derived_stats.goals_by_category
    # Should have entries for retirement, child_local_education, child_marriage
    assert "retirement" in cats
    assert "child_local_education" in cats
    assert "child_marriage" in cats
    # child_local_education has 1 goal (₹25L PV)
    assert cats["child_local_education"].count == 1
    assert cats["child_local_education"].total_amount_pv == 2_500_000


def test_debt_free_date_with_existing_mortgage():
    inp = _baseline_with_goals().model_copy(update={
        "current_properties": [CurrentProperty(
            name="home", has_mortgage=True,
            mortgage_balance=4_500_000, mortgage_emi=42_000,
            mortgage_last_date=date(2040, 5, 9),
        )],
    })
    out = compute_full_projection(inp)
    # debt_free_date should equal the mortgage's last month_end
    assert out.derived_stats.debt_free_date is not None
    assert out.derived_stats.debt_free_date.year >= 2040


def test_no_mortgages_means_no_debt_free_date():
    out = compute_full_projection(_baseline_with_goals())
    assert out.derived_stats.debt_free_date is None


def test_peak_nfa_is_actually_max():
    """Sanity: the peak should be ≥ NFA at any other arbitrary point."""
    out = compute_full_projection(_baseline_with_goals())
    # Use detail_level="full" to get nfa_monthly_series for cross-checking
    out_full = compute_full_projection(
        _baseline_with_goals().model_copy(update={"detail_level": "full"})
    )
    series = out_full.nfa_monthly_series
    assert series is not None
    actual_peak = max(r.nfa_close for r in series)
    assert out_full.derived_stats.peak_nfa_amount == actual_peak
