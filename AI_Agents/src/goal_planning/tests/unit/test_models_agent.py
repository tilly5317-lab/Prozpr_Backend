from datetime import date
import pytest
from pydantic import ValidationError, TypeAdapter
from goal_planning.models import (
    NumericOverride, RateOverride, PerGoalRateOverride, PropertyFieldOverride,
    OverrideSpec, GoalMutation, LeverAction, Lever, HeadlineStatus,
    ExtractedFinancialEvent, ExtractedGoal, ExtractedProperty, ExtractedCashflow,
    ExtractedMutation, ExtractionError,
    CustomGoal, GoalProperty, OneOffEvent, GoalType,
)


def test_numeric_override_rejects_invalid_key():
    with pytest.raises(ValidationError):
        NumericOverride(kind="numeric", key="retirement_age", value=58)


def test_numeric_override_accepts_valid_key():
    n = NumericOverride(kind="numeric", key="monthly_investment_next_12m", value=50_000)
    assert n.value == 50_000


def test_property_field_override_includes_early_payoff_date():
    o = PropertyFieldOverride(
        kind="property_field", property_name="apartment_1",
        field="early_payoff_date", value=date(2030, 5, 9),
    )
    assert o.field == "early_payoff_date"


def test_override_spec_discriminator():
    adapter = TypeAdapter(OverrideSpec)
    parsed = adapter.validate_python({
        "kind": "rate", "key": "inflation_property", "value": 0.07,
    })
    assert isinstance(parsed, RateOverride)


def test_goal_mutation_fields():
    m = GoalMutation(kind="mutation", op="update", goal_name="retirement", fields={"retirement_age": 58})
    assert m.fields["retirement_age"] == 58


def test_lever_action_union_supports_mutation():
    adapter = TypeAdapter(LeverAction)
    parsed = adapter.validate_python({
        "kind": "mutation", "op": "update",
        "goal_name": "retirement", "fields": {"retirement_age": 62},
    })
    assert isinstance(parsed, GoalMutation)


def test_extracted_event_discriminator():
    adapter = TypeAdapter(ExtractedFinancialEvent)
    g = adapter.validate_python({
        "kind": "custom_goal",
        "goal": {
            "name": "college", "goal_type": "child_local_education",
            "amount_pv": 1_000_000, "goal_date": "2035-01-01",
        },
    })
    assert isinstance(g, ExtractedGoal)


def test_dated_field_for_each_kind():
    from datetime import date
    g = ExtractedGoal(kind="custom_goal", goal=CustomGoal(
        name="x", goal_type=GoalType.custom, amount_pv=1_000_000, goal_date=date(2035, 1, 1),
    ))
    assert g.dated_field() == date(2035, 1, 1)

    p = ExtractedProperty(kind="property_goal", property=GoalProperty(
        name="x", target_pv=10_000_000, goal_date=date(2030, 1, 1),
    ), assumptions_used=[])
    assert p.dated_field() == date(2030, 1, 1)

    c = ExtractedCashflow(kind="cashflow_event", event=OneOffEvent(
        description="bonus", amount=100_000, date=date(2027, 3, 1),
    ), direction="in", confidence="high")
    assert c.dated_field() == date(2027, 3, 1)

    m = ExtractedMutation(kind="goal_mutation", op="update", goal_name="g", fields={"goal_date": date(2040, 1, 1)})
    assert m.dated_field() == date(2040, 1, 1)

    m_no_date = ExtractedMutation(kind="goal_mutation", op="update", goal_name="g", fields={"amount_pv": 2_000_000})
    assert m_no_date.dated_field() is None
