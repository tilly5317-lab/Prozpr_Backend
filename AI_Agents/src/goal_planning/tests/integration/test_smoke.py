"""End-to-end smoke test: import → engine projection → agent response."""
from datetime import date


def test_smoke_engine():
    from goal_planning import (
        compute_full_projection, GoalPlanningInput, ClientProfile, RetirementInput,
    )
    inp = GoalPlanningInput(
        profile=ClientProfile(
            latest_update_date=date(2026, 5, 9), annual_income=2_000_000, tax_rate=0.30,
            financial_assets=20_000_000, financial_liabilities_excl_mortgage=5_000_000,
            monthly_household_expense=80_000,
        ),
        retirement=RetirementInput(date_of_birth=date(1976, 5, 9)),
    )
    out = compute_full_projection(inp)
    assert out.engine_version is not None
    assert out.headline.number_of_goals >= 1


def test_smoke_validate_input_only_catches_past_goal_date():
    from goal_planning import (
        validate_input_only, GoalPlanningInput, ClientProfile, RetirementInput,
        CustomGoal, GoalType,
    )
    inp = GoalPlanningInput(
        profile=ClientProfile(
            latest_update_date=date(2026, 5, 9), annual_income=2_000_000, tax_rate=0.30,
            financial_assets=20_000_000, financial_liabilities_excl_mortgage=5_000_000,
            monthly_household_expense=80_000,
        ),
        retirement=RetirementInput(date_of_birth=date(1976, 5, 9)),
        custom_goals=[CustomGoal(
            name="past_goal", goal_type=GoalType.custom,
            amount_pv=1_000_000, goal_date=date(2024, 1, 1),
        )],
    )
    issues = validate_input_only(inp)
    assert any(i.severity == "error" and "past" in i.message.lower() for i in issues)
