"""Unit tests for the goal_planning input builder.

Uses lightweight stand-in objects rather than the ORM models so the test runs
without a database. The real ORM rows expose the same attribute names; the
builder reads attributes, never queries.
"""
from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest

from app.services.ai_bridge.goal_planning.input_builder import (
    build_goal_planning_input_for_user,
)


def _user(
    *,
    dob: date | None = date(1985, 6, 15),
    inv: object | None = None,
    tax: object | None = None,
    goals: list[object] | None = None,
):
    return SimpleNamespace(
        date_of_birth=dob,
        investment_profile=inv,
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
    inv = SimpleNamespace(
        annual_income=2_500_000,
        regular_outgoings=80_000,
        investable_assets=10_000_000,
        total_liabilities=500_000,
        monthly_savings=40_000,
        retirement_age=62,
        target_corpus=15_000_000,
    )
    tax = SimpleNamespace(income_tax_rate=28.0)  # stored as percentage
    goals = [
        _goal(name="kid_education", goal_type="CHILD_EDUCATION",
              pv=3_000_000, target=date(2038, 7, 1)),
        _goal(name="vacation", goal_type="TRAVEL",
              pv=500_000, target=date(2029, 12, 1)),
    ]
    user = _user(inv=inv, tax=tax, goals=goals)
    inp, debug = build_goal_planning_input_for_user(user, anchor_date=date(2026, 5, 15))

    assert inp.profile.annual_income == 2_500_000
    assert inp.profile.effective_tax_rate == 0.28
    assert inp.profile.financial_assets == 10_000_000
    assert inp.profile.starting_monthly_investment == 40_000
    assert inp.retirement.retirement_age == 62
    assert inp.retirement.retirement_corpus_pv_today_override == 15_000_000
    assert len(inp.custom_goals) == 2
    names = {g.name for g in inp.custom_goals}
    assert names == {"kid_education", "vacation"}
    # CHILD_EDUCATION → child_local_education; TRAVEL → custom
    kid = next(g for g in inp.custom_goals if g.name == "kid_education")
    assert kid.goal_type.value == "child_local_education"
    vac = next(g for g in inp.custom_goals if g.name == "vacation")
    assert vac.goal_type.value == "custom"
    # ORM inflation 6.0 → engine 0.06 per-goal override
    assert kid.inflation_rate_override == 0.06
    assert debug["active_goal_count"] == 2
    assert debug["validation_issues"] == []  # no defaults fired


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
    inv = SimpleNamespace(annual_income=1_000_000, regular_outgoings=30_000)
    user = _user(inv=inv, goals=goals)
    inp, debug = build_goal_planning_input_for_user(user, anchor_date=today)
    assert [g.name for g in inp.custom_goals] == ["live_goal"]
    assert debug["active_goal_count"] == 1


def test_missing_tax_profile_emits_validation_issue_and_default():
    inv = SimpleNamespace(annual_income=1_000_000, regular_outgoings=30_000)
    user = _user(inv=inv, tax=None)
    inp, debug = build_goal_planning_input_for_user(user, anchor_date=date(2026, 5, 15))
    assert inp.profile.effective_tax_rate == 0.25
    assert any("tax rate" in v for v in debug["validation_issues"])
    assert "tax_profile_missing" in debug["defaults_applied"]


def test_home_purchase_emits_property_validation_issue():
    inv = SimpleNamespace(annual_income=1_000_000, regular_outgoings=30_000)
    goals = [_goal(name="dream_home", goal_type="HOME_PURCHASE",
                   pv=15_000_000, target=date(2032, 4, 1))]
    user = _user(inv=inv, goals=goals)
    inp, debug = build_goal_planning_input_for_user(user, anchor_date=date(2026, 5, 15))
    assert inp.custom_goals[0].goal_type.value == "property"
    assert any("HOME_PURCHASE" in v for v in debug["validation_issues"])


def test_output_is_engine_consumable():
    """End-to-end sanity: the built input runs through the engine without error."""
    from cashflow_statement import compute_full_projection

    inv = SimpleNamespace(
        annual_income=2_000_000, regular_outgoings=50_000,
        investable_assets=5_000_000, total_liabilities=0,
        monthly_savings=30_000, retirement_age=60,
    )
    tax = SimpleNamespace(income_tax_rate=25.0)
    goals = [_goal(name="travel", goal_type="TRAVEL",
                   pv=500_000, target=date(2030, 6, 1))]
    user = _user(inv=inv, tax=tax, goals=goals)
    inp, _ = build_goal_planning_input_for_user(user, anchor_date=date(2026, 5, 15))

    out = compute_full_projection(inp)
    assert out.headline.number_of_goals >= 1
    assert out.headline.last_goal_date is not None
