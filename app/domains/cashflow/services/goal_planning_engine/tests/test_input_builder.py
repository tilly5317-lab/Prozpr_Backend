"""Unit tests for the goal_planning input builder."""
from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest

from app.domains.cashflow.services.goal_planning_engine.input_builder import (
    build_goal_planning_input_for_user,
)


def _user(
    *,
    dob: date | None = date(1985, 6, 15),
    pfp: object | None = None,
    inv: object | None = None,
    tax: object | None = None,
    goals: list[object] | None = None,
    properties: list[object] | None = None,
):
    inv_profile = inv
    if inv_profile is not None and properties is not None:
        inv_profile = SimpleNamespace(**vars(inv), current_properties=properties)
    elif inv_profile is None and properties:
        inv_profile = SimpleNamespace(current_properties=properties)
    return SimpleNamespace(
        date_of_birth=dob,
        assumed_lifespan_years=85,
        personal_finance_profile=pfp,
        investment_profile=inv_profile,
        tax_profile=tax,
        financial_goals=goals or [],
    )


def _goal(
    *, name: str, goal_type: str, pv: float, target: date,
    status: str = "ACTIVE", inflation: float | None = 6.0,
):
    return SimpleNamespace(
        goal_name=name,
        goal_type=SimpleNamespace(value=goal_type),
        present_value_amount=pv,
        target_date=target,
        status=SimpleNamespace(value=status),
        inflation_rate=inflation,
    )


def test_missing_dob_raises():
    user = _user(dob=None)
    with pytest.raises(ValueError, match="missing_date_of_birth"):
        build_goal_planning_input_for_user(user, anchor_date=date(2026, 5, 15))


def test_happy_path_maps_all_fields():
    pfp = SimpleNamespace(
        annual_income=2_500_000,
        monthly_household_expense=80_000,
        financial_assets=10_000_000,
        financial_liabilities_excl_mortgage=500_000,
        starting_monthly_investment=40_000,
        effective_tax_rate=0.28,
    )
    inv = SimpleNamespace(retirement_age=62, target_corpus=15_000_000)
    goals = [
        _goal(name="kid_education", goal_type="CHILD_EDUCATION",
              pv=3_000_000, target=date(2038, 7, 1)),
        _goal(name="vacation", goal_type="TRAVEL",
              pv=500_000, target=date(2029, 12, 1)),
    ]
    user = _user(pfp=pfp, inv=inv, goals=goals)
    inp, debug = build_goal_planning_input_for_user(user, anchor_date=date(2026, 5, 15))

    assert inp.profile.annual_income == 2_500_000
    assert inp.profile.effective_tax_rate == 0.28
    assert inp.profile.financial_assets == 10_000_000
    assert inp.profile.starting_monthly_investment == 40_000
    assert inp.retirement.retirement_age == 62
    assert inp.retirement.retirement_corpus_pv_today_override == 15_000_000
    assert len(inp.custom_goals) == 2
    assert debug["active_goal_count"] == 2
    assert debug["validation_issues"] == []


def test_maps_current_properties_from_investment_profile():
    pfp = SimpleNamespace(
        annual_income=1_000_000,
        monthly_household_expense=30_000,
        financial_assets=500_000,
        financial_liabilities_excl_mortgage=0,
        effective_tax_rate=0.25,
    )
    props = [
        SimpleNamespace(
            name="Primary home",
            property_value=8_000_000,
            has_mortgage=True,
            mortgage_emi=45_000,
            mortgage_end_date=date(2038, 3, 31),
        ),
    ]
    user = _user(pfp=pfp, inv=SimpleNamespace(retirement_age=60), properties=props)
    inp, debug = build_goal_planning_input_for_user(user, anchor_date=date(2026, 5, 15))
    assert len(inp.current_properties) == 1
    assert inp.current_properties[0].name == "Primary home"
    assert debug["current_property_count"] == 1
    assert "current_properties=[]" not in debug["defaults_applied"]


def test_skips_retirement_inactive_and_past_goals():
    today = date(2026, 5, 15)
    goals = [
        _goal(name="my_retirement", goal_type="RETIREMENT",
              pv=20_000_000, target=date(2045, 1, 1)),
        _goal(name="achieved_goal", goal_type="OTHER",
              pv=100_000, target=date(2030, 1, 1), status="ACHIEVED"),
        _goal(name="past_goal", goal_type="OTHER",
              pv=100_000, target=date(2024, 1, 1)),
        _goal(name="live_goal", goal_type="VEHICLE",
              pv=500_000, target=date(2030, 6, 1)),
    ]
    pfp = SimpleNamespace(
        annual_income=1_000_000,
        monthly_household_expense=30_000,
        financial_assets=0,
        financial_liabilities_excl_mortgage=0,
        effective_tax_rate=0.25,
    )
    user = _user(pfp=pfp, inv=SimpleNamespace(retirement_age=60), goals=goals)
    inp, debug = build_goal_planning_input_for_user(user, anchor_date=today)
    assert [g.name for g in inp.custom_goals] == ["live_goal"]
    assert debug["active_goal_count"] == 1


def test_missing_tax_profile_emits_validation_issue_and_default():
    pfp = SimpleNamespace(
        annual_income=1_000_000,
        monthly_household_expense=30_000,
        financial_assets=0,
        financial_liabilities_excl_mortgage=0,
        effective_tax_rate=None,
    )
    user = _user(pfp=pfp, inv=SimpleNamespace(retirement_age=60), tax=None)
    inp, debug = build_goal_planning_input_for_user(user, anchor_date=date(2026, 5, 15))
    assert inp.profile.effective_tax_rate == 0.25
    assert any("tax rate" in v for v in debug["validation_issues"])
    assert "tax_profile_missing" in debug["defaults_applied"]


def test_home_purchase_emits_property_validation_issue():
    pfp = SimpleNamespace(
        annual_income=1_000_000,
        monthly_household_expense=30_000,
        financial_assets=0,
        financial_liabilities_excl_mortgage=0,
        effective_tax_rate=0.25,
    )
    goals = [_goal(name="dream_home", goal_type="HOME_PURCHASE",
                   pv=15_000_000, target=date(2032, 4, 1))]
    user = _user(pfp=pfp, inv=SimpleNamespace(retirement_age=60), goals=goals)
    inp, debug = build_goal_planning_input_for_user(user, anchor_date=date(2026, 5, 15))
    assert inp.custom_goals[0].goal_type.value == "property"
    assert any("HOME_PURCHASE" in v for v in debug["validation_issues"])


def test_skips_retirement_by_name_not_only_goal_type():
    """A goal named 'Retirement' must not duplicate engine RetirementInput."""
    today = date(2026, 5, 15)
    goals = [
        _goal(name="Retirement", goal_type="OTHER",
              pv=20_000_000, target=date(2045, 1, 1)),
        _goal(name="travel", goal_type="TRAVEL",
              pv=500_000, target=date(2030, 6, 1)),
    ]
    pfp = SimpleNamespace(
        annual_income=1_000_000,
        monthly_household_expense=30_000,
        financial_assets=0,
        financial_liabilities_excl_mortgage=0,
        effective_tax_rate=0.25,
    )
    user = _user(pfp=pfp, inv=SimpleNamespace(retirement_age=60), goals=goals)
    inp, debug = build_goal_planning_input_for_user(user, anchor_date=today)
    assert [g.name for g in inp.custom_goals] == ["travel"]
    assert any("skipped" in v and "retirement" in v.lower() for v in debug["validation_issues"])


def test_output_is_engine_consumable():
    from cashflow_statement import compute_full_projection

    pfp = SimpleNamespace(
        annual_income=2_000_000,
        monthly_household_expense=50_000,
        financial_assets=5_000_000,
        financial_liabilities_excl_mortgage=0,
        starting_monthly_investment=30_000,
        effective_tax_rate=0.25,
    )
    inv = SimpleNamespace(retirement_age=60)
    goals = [_goal(name="travel", goal_type="TRAVEL",
                   pv=500_000, target=date(2030, 6, 1))]
    user = _user(pfp=pfp, inv=inv, tax=SimpleNamespace(income_tax_rate=25.0), goals=goals)
    inp, _ = build_goal_planning_input_for_user(user, anchor_date=date(2026, 5, 15))

    out = compute_full_projection(inp)
    assert out.headline.number_of_goals >= 1
    assert out.headline.last_goal_date is not None
