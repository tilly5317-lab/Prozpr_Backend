"""Counterfactual overrides applied to a built GoalPlanningInput.

The engine is pure Python and cheap to re-run, so a "what if I retire at 50?"
question is answered by rebuilding the input with one field changed and running
it again — not by asking the formatter to reason about a plan it has no numbers
for. Keys are whitelisted: the detector is an LLM, and an unrecognised key must
fail loudly rather than silently model something the customer did not ask for.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.domains.cashflow.services.goal_planning_engine.overrides import (
    ALLOWED_OVERRIDE_KEYS,
    apply_overrides,
)


def _input():
    from app.domains.ai_engine.common import ensure_ai_agents_path

    ensure_ai_agents_path()
    from cashflow_statement.models import (
        ClientProfile,
        GoalPlanningInput,
        RetirementInput,
    )

    return GoalPlanningInput(
        profile=ClientProfile(
            annual_income=10_000_000,
            effective_tax_rate=0.30,
            financial_assets=48_300_000,
            financial_liabilities_excl_mortgage=0,
            monthly_household_expense=200_000,
            starting_monthly_investment=200_000,
        ),
        retirement=RetirementInput(
            date_of_birth=date(1992, 7, 8),
            retirement_age=55,
            assumed_lifespan_years=85,
        ),
    )


def test_none_returns_the_input_unchanged():
    base = _input()

    assert apply_overrides(base, None) is base


def test_retirement_age_is_overridden():
    result = apply_overrides(_input(), {"retirement_age": 50})

    assert result.retirement.retirement_age == 50
    assert result.retirement.date_of_birth == date(1992, 7, 8), "siblings survive"


def test_monthly_investment_is_overridden():
    result = apply_overrides(_input(), {"starting_monthly_investment": 250_000})

    assert result.profile.starting_monthly_investment == 250_000
    assert result.profile.annual_income == 10_000_000, "siblings survive"


def test_a_one_off_outflow_is_appended_not_replaced():
    """'Can I afford a ₹10L trip next year?' — the customer's real question."""
    result = apply_overrides(
        _input(),
        {"one_off_outflow": {"description": "Trip", "amount": 1_000_000, "date": "2027-06-01"}},
    )

    assert len(result.one_off_outflows) == 1
    assert result.one_off_outflows[0].amount == 1_000_000
    assert result.one_off_outflows[0].description == "Trip"


def test_the_base_input_is_never_mutated():
    base = _input()

    apply_overrides(base, {"retirement_age": 50})

    assert base.retirement.retirement_age == 55


def test_an_unknown_key_is_rejected():
    with pytest.raises(ValueError, match="unknown override key"):
        apply_overrides(_input(), {"effective_risk_score": 9.0})


def test_allowed_keys_are_the_documented_four():
    assert ALLOWED_OVERRIDE_KEYS == frozenset(
        {
            "retirement_age",
            "starting_monthly_investment",
            "monthly_household_expense",
            "one_off_outflow",
        }
    )
