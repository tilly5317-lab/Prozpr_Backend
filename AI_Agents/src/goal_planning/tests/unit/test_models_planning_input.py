from datetime import date
import pytest
from pydantic import ValidationError
from goal_planning.models import (
    GoalPlanningInput, ClientProfile, RetirementInput, CustomGoal, GoalType,
    GoalProperty, CurrentProperty, OneOffEvent,
)


def _profile():
    return ClientProfile(
        latest_update_date=date(2026, 5, 9), annual_income=2_000_000, tax_rate=0.30,
        financial_assets=20_000_000, financial_liabilities_excl_mortgage=5_000_000,
        monthly_household_expense=80_000,
    )


def _retirement():
    return RetirementInput(date_of_birth=date(1976, 5, 9))


def test_input_minimal_construction():
    inp = GoalPlanningInput(profile=_profile(), retirement=_retirement())
    assert inp.detail_level == "default"
    assert inp.assumptions.roi_long_term_post_tax == 0.09


def test_input_rejects_duplicate_goal_names_case_insensitive():
    with pytest.raises(ValidationError, match="Duplicate names"):
        GoalPlanningInput(
            profile=_profile(),
            retirement=_retirement(),
            custom_goals=[
                CustomGoal(name="College", goal_type=GoalType.custom, amount_pv=1_000_000, goal_date=date(2035, 1, 1)),
                CustomGoal(name="college", goal_type=GoalType.custom, amount_pv=2_000_000, goal_date=date(2040, 1, 1)),
            ],
        )


def test_input_rejects_name_collision_with_retirement():
    with pytest.raises(ValidationError, match="Duplicate names"):
        GoalPlanningInput(
            profile=_profile(),
            retirement=_retirement(),
            custom_goals=[
                CustomGoal(name="Retirement", goal_type=GoalType.custom, amount_pv=1_000_000, goal_date=date(2040, 1, 1)),
            ],
        )


def test_input_rejects_property_name_collision_with_oneoff():
    with pytest.raises(ValidationError, match="Duplicate names"):
        GoalPlanningInput(
            profile=_profile(),
            retirement=_retirement(),
            current_properties=[CurrentProperty(name="Mumbai_house", has_mortgage=False)],
            one_off_inflows=[OneOffEvent(description="mumbai_house", amount=500_000, date=date(2027, 1, 1))],
        )


def test_input_accepts_unique_names():
    inp = GoalPlanningInput(
        profile=_profile(),
        retirement=_retirement(),
        custom_goals=[
            CustomGoal(name="college", goal_type=GoalType.custom, amount_pv=1_000_000, goal_date=date(2035, 1, 1)),
            CustomGoal(name="marriage", goal_type=GoalType.child_marriage, amount_pv=2_000_000, goal_date=date(2045, 1, 1)),
        ],
    )
    assert len(inp.custom_goals) == 2
