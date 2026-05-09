from datetime import date
from goal_planning.models import (
    GoalPlanningInput, ClientProfile, RetirementInput, CustomGoal, GoalType,
)
from goal_planning.engine import compute_full_projection
from goal_planning.agent.levers import generate_lever_a_increase_sip


def _shortfall_input():
    return GoalPlanningInput(
        profile=ClientProfile(
            latest_update_date=date(2026, 5, 9), annual_income=2_000_000, tax_rate=0.30,
            financial_assets=5_000_000, financial_liabilities_excl_mortgage=0,
            monthly_household_expense=80_000, monthly_investment_next_12m=20_000,
        ),
        retirement=RetirementInput(date_of_birth=date(1976, 5, 9)),
        custom_goals=[CustomGoal(
            name="big_goal", goal_type=GoalType.custom,
            amount_pv=10_000_000, goal_date=date(2035, 1, 1),
        )],
    )


def test_lever_a_finds_feasible_sip_when_one_exists():
    inp = _shortfall_input()
    out = compute_full_projection(inp)
    lever = generate_lever_a_increase_sip(inp, out, sip_max_multiplier=5.0)
    if lever is not None:
        assert lever.action.kind == "numeric"
        assert lever.action.key == "monthly_investment_next_12m"


def test_lever_a_returns_none_when_already_feasible():
    inp = GoalPlanningInput(
        profile=ClientProfile(
            latest_update_date=date(2026, 5, 9), annual_income=10_000_000, tax_rate=0.30,
            financial_assets=100_000_000, financial_liabilities_excl_mortgage=0,
            monthly_household_expense=80_000, monthly_investment_next_12m=200_000,
        ),
        retirement=RetirementInput(date_of_birth=date(1976, 5, 9)),
    )
    out = compute_full_projection(inp)
    lever = generate_lever_a_increase_sip(inp, out, sip_max_multiplier=5.0)
    assert lever is None
