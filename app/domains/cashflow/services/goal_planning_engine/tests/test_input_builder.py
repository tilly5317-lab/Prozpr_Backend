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
    *,
    name: str,
    goal_type: str,
    pv: float,
    target: date,
    status: str = "ACTIVE",
    inflation: float | None = 6.0,
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
        _goal(
            name="kid_education",
            goal_type="CHILD_EDUCATION",
            pv=3_000_000,
            target=date(2038, 7, 1),
        ),
        _goal(
            name="vacation", goal_type="TRAVEL", pv=500_000, target=date(2029, 12, 1)
        ),
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


def test_skips_inactive_and_past_goals():
    """Inactive (ACHIEVED) and past-dated goals are skipped. A RETIREMENT-typed
    goal is NOT skipped any more — it flows through as a custom goal."""
    today = date(2026, 5, 15)
    goals = [
        _goal(
            name="my_retirement",
            goal_type="RETIREMENT",
            pv=20_000_000,
            target=date(2045, 1, 1),
        ),
        _goal(
            name="achieved_goal",
            goal_type="OTHER",
            pv=100_000,
            target=date(2030, 1, 1),
            status="ACHIEVED",
        ),
        _goal(name="past_goal", goal_type="OTHER", pv=100_000, target=date(2024, 1, 1)),
        _goal(
            name="live_goal", goal_type="VEHICLE", pv=500_000, target=date(2030, 6, 1)
        ),
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
    assert sorted(g.name for g in inp.custom_goals) == ["live_goal", "my_retirement"]
    assert debug["active_goal_count"] == 2


def test_missing_tax_rate_blocks_engine():
    """No tax rate anywhere → the engine refuses rather than defaulting to 25%."""
    pfp = SimpleNamespace(
        annual_income=1_000_000,
        monthly_household_expense=30_000,
        financial_assets=0,
        financial_liabilities_excl_mortgage=0,
        effective_tax_rate=None,
    )
    user = _user(pfp=pfp, inv=SimpleNamespace(retirement_age=60), tax=None)
    with pytest.raises(
        ValueError, match="missing_required_inputs:.*effective_tax_rate"
    ):
        build_goal_planning_input_for_user(user, anchor_date=date(2026, 5, 15))


def test_missing_finance_profile_blocks_engine():
    """No personal finance profile → engine refuses, never zero-fills income/assets."""
    user = _user(pfp=None, inv=SimpleNamespace(retirement_age=60))
    with pytest.raises(ValueError, match="missing_required_inputs"):
        build_goal_planning_input_for_user(user, anchor_date=date(2026, 5, 15))


def test_missing_retirement_age_defaults_to_60():
    """retirement_age is optional — when absent it defaults to 60 (a standard
    planning assumption, like lifespan=100) rather than blocking the engine."""
    pfp = SimpleNamespace(
        annual_income=1_000_000,
        monthly_household_expense=30_000,
        financial_assets=0,
        financial_liabilities_excl_mortgage=0,
        effective_tax_rate=0.25,
    )
    user = _user(pfp=pfp, inv=None)
    inp, debug = build_goal_planning_input_for_user(user, anchor_date=date(2026, 5, 15))
    assert inp.retirement.retirement_age == 60
    assert any("retirement_age=60" in d for d in debug["defaults_applied"])


def test_horizon_extends_to_retirement_even_when_goals_are_earlier():
    """The cashflow projection must run to max(last_goal, retirement_age). With a
    DOB of 1985 + retirement age 60 (retirement ~2045) and the only goal in 2030,
    the projection must still reach ~retirement, not stop at the 2030 goal."""
    from cashflow_statement import compute_full_projection

    pfp = SimpleNamespace(
        annual_income=2_000_000,
        monthly_household_expense=50_000,
        financial_assets=5_000_000,
        financial_liabilities_excl_mortgage=0,
        starting_monthly_investment=30_000,
        effective_tax_rate=0.25,
    )
    goals = [
        _goal(name="travel", goal_type="TRAVEL", pv=500_000, target=date(2030, 6, 1))
    ]
    # DOB 1985-06-15 + retirement age 60 -> retires 2045 (FY2046).
    user = _user(pfp=pfp, inv=SimpleNamespace(retirement_age=60), goals=goals)
    inp, _ = build_goal_planning_input_for_user(user, anchor_date=date(2026, 5, 15))

    out = compute_full_projection(inp)
    last_fy_year = max(r.fy_end_date.year for r in out.annual_cashflow)
    # Goal is in 2030; the horizon must extend well past it, up to retirement.
    assert last_fy_year >= 2045, f"horizon stopped at FY{last_fy_year}, expected >= 2045"


def test_home_purchase_emits_property_validation_issue():
    pfp = SimpleNamespace(
        annual_income=1_000_000,
        monthly_household_expense=30_000,
        financial_assets=0,
        financial_liabilities_excl_mortgage=0,
        effective_tax_rate=0.25,
    )
    goals = [
        _goal(
            name="dream_home",
            goal_type="HOME_PURCHASE",
            pv=15_000_000,
            target=date(2032, 4, 1),
        )
    ]
    user = _user(pfp=pfp, inv=SimpleNamespace(retirement_age=60), goals=goals)
    inp, debug = build_goal_planning_input_for_user(user, anchor_date=date(2026, 5, 15))
    assert inp.custom_goals[0].goal_type.value == "property"
    assert any("HOME_PURCHASE" in v for v in debug["validation_issues"])


def test_user_retirement_goal_flows_through_as_custom_goal():
    """Retirement is no longer injected from the profile — a user goal named
    'Retirement' now flows through as a normal custom goal (model_retirement=False),
    so the projection considers retirement only when the user adds it."""
    today = date(2026, 5, 15)
    goals = [
        _goal(
            name="Retirement", goal_type="OTHER", pv=20_000_000, target=date(2045, 1, 1)
        ),
        _goal(name="travel", goal_type="TRAVEL", pv=500_000, target=date(2030, 6, 1)),
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
    # Both goals pass through; nothing is skipped as "retirement".
    assert sorted(g.name for g in inp.custom_goals) == ["Retirement", "travel"]
    assert inp.model_retirement is False
    assert not any(
        "skipped" in v and "retirement" in v.lower() for v in debug["validation_issues"]
    )


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
    goals = [
        _goal(name="travel", goal_type="TRAVEL", pv=500_000, target=date(2030, 6, 1))
    ]
    user = _user(
        pfp=pfp, inv=inv, tax=SimpleNamespace(income_tax_rate=25.0), goals=goals
    )
    inp, _ = build_goal_planning_input_for_user(user, anchor_date=date(2026, 5, 15))

    out = compute_full_projection(inp)
    assert out.headline.number_of_goals >= 1
    assert out.headline.last_goal_date is not None
