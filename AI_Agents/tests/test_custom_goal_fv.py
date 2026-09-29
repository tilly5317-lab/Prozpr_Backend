from datetime import date

from cashflow_statement import Assumptions, CustomGoal, GoalType, custom_goal_fv
from cashflow_statement.engine.dates import _round_thousand
from financial_primitives.inflation import inflate

TODAY = date(2026, 9, 28)
YEARS = (date(2028, 3, 31) - TODAY).days / 365


def _goal(**kw):
    base = dict(name="Car", goal_type=GoalType.custom, goal_value_pv=500_000.0,
                goal_date=date(2028, 3, 15))
    base.update(kw)
    return CustomGoal(**base)


def test_inflates_to_end_of_goal_month_at_the_type_default_and_rounds():
    a = Assumptions()
    assert custom_goal_fv(_goal(), a, TODAY) == _round_thousand(
        inflate(500_000.0, a.inflation_household_expense, YEARS)
    )


def test_per_goal_override_wins():
    assert custom_goal_fv(_goal(inflation_rate_override=0.10), Assumptions(), TODAY) == (
        _round_thousand(inflate(500_000.0, 0.10, YEARS))
    )


def test_property_goal_uses_property_inflation():
    a = Assumptions(inflation_property=0.09)
    assert custom_goal_fv(_goal(goal_type=GoalType.property), a, TODAY) == _round_thousand(
        inflate(500_000.0, 0.09, YEARS)
    )


def test_given_future_value_is_returned_as_is():
    assert custom_goal_fv(_goal(goal_value_fv=777_000.0), Assumptions(), TODAY) == 777_000.0
