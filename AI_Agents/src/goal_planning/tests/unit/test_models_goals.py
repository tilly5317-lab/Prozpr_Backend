from datetime import date
import pytest
from pydantic import ValidationError
from goal_planning.models import (
    GoalType, CurrentProperty, GoalProperty, CustomGoal, OneOffEvent,
)


def test_goal_type_enum_values():
    assert GoalType.retirement.value == "retirement"
    assert GoalType.property.value == "property"
    assert GoalType.child_abroad_education.value == "child_abroad_education"
    assert GoalType.child_local_education.value == "child_local_education"
    assert GoalType.child_marriage.value == "child_marriage"
    assert GoalType.custom.value == "custom"


def test_current_property_defaults():
    p = CurrentProperty(name="apartment_1", has_mortgage=False)
    assert p.mortgage_balance is None
    assert p.mortgage_balance_as_of_date is None


def test_goal_property_defaults_cash_purchase():
    p = GoalProperty(name="house_1", target_pv=10_000_000, goal_date=date(2030, 5, 9))
    assert p.is_downpayment_only is False
    assert p.upfront_amount is None
    assert p.mortgage_tenure_years == 0
    assert p.mortgage_interest_annual == 0.075


def test_goal_property_requires_pv_or_fv():
    with pytest.raises(ValidationError):
        GoalProperty(name="house", goal_date=date(2030, 1, 1))


def test_goal_property_downpayment_requires_upfront():
    with pytest.raises(ValidationError, match="upfront_amount required"):
        GoalProperty(
            name="h", target_pv=10_000_000, is_downpayment_only=True,
            goal_date=date(2030, 1, 1), mortgage_tenure_years=20,
        )


def test_goal_property_downpayment_requires_tenure():
    with pytest.raises(ValidationError, match="mortgage_tenure_years"):
        GoalProperty(
            name="h", target_pv=10_000_000, is_downpayment_only=True,
            upfront_amount=2_000_000, goal_date=date(2030, 1, 1),
            mortgage_tenure_years=0,
        )


def test_custom_goal_requires_pv_or_fv():
    with pytest.raises(ValidationError):
        CustomGoal(name="g", goal_type=GoalType.custom, goal_date=date(2030, 1, 1))


def test_custom_goal_pv_or_fv_either_works():
    g_pv = CustomGoal(name="g", goal_type=GoalType.custom, amount_pv=1_000_000, goal_date=date(2030, 1, 1))
    g_fv = CustomGoal(name="g", goal_type=GoalType.custom, amount_fv=1_500_000, goal_date=date(2030, 1, 1))
    assert g_pv.amount_pv == 1_000_000
    assert g_fv.amount_fv == 1_500_000


def test_oneoff_event():
    e = OneOffEvent(description="bonus", amount=500_000, date=date(2027, 3, 1))
    assert e.amount == 500_000
