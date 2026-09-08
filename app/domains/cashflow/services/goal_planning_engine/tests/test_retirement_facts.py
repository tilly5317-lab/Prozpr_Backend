"""The retirement block must not be mistakable for the customer's retirement goal.

On the product path ``model_retirement=False`` (input_builder.py:248): retirement is
NOT injected as a goal, income never stops, and ``retirement_date`` serves only as
the projection-horizon floor. Retirement is funded only if the customer created a
goal for it themselves.

The pack shipped that block anyway, corpus figures included. For a real user that
produced two conflicting answers side by side — ``retirement.corpus_required_used``
= ₹16.37 crore, which ``goals_table.py:56`` proves is only used when
``include_retirement`` is true, against their actual "Retirement" goal needing
₹45.58 crore. Two dates too (2047-07-08 vs 2052-07-01) and no age anywhere, so the
formatter picked the goal's date and invented "age 52".
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.domains.cashflow.services.goal_planning_engine.service import (
    _build_facts_pack,
)


def _output():
    """Minimal stand-in for GoalPlanningOutput's shape."""
    from datetime import date

    money = SimpleNamespace
    return SimpleNamespace(
        headline=money(
            years_to_last_goal=26, last_goal_date=date(2052, 7, 1), number_of_goals=2,
            corpus_today=48_300_000, total_corpus_required_today=50_300_000,
            surplus_or_shortfall_today=-2_000_000, corpus_closing=423_700_000,
            is_feasible=True, total_shortfall_fv=0, total_funded_amount=458_300_000,
        ),
        retirement=money(
            retirement_date=date(2047, 7, 8), years_to_retirement=20.96,
            corpus_required_used=163_749_000, corpus_required_pv_today=48_090_819,
            annual_household_expense_today=2_400_000, post_retirement_years=30,
        ),
        fund_flow_summary=money(
            corpus_opening=48_300_000, total_investments=197_200_000,
            total_roi=636_500_000, total_one_off_in=0, total_one_off_out=0,
            total_goals_paid=458_300_000, corpus_closing=423_700_000,
        ),
        goals=[],
        annual_cashflow=[],
    )


def _user():
    from datetime import date

    return SimpleNamespace(first_name="X", date_of_birth=date(1992, 7, 8))


def test_retirement_age_is_a_fact_not_a_derivation():
    """The house rule is that the formatter quotes, never computes."""
    facts = _build_facts_pack(_output(), _user(), retirement_age=55, retirement_modelled=False)

    assert facts["retirement"]["planned_retirement_age_from_profile"] == 55


def test_unmodelled_retirement_drops_its_corpus_figures():
    facts = _build_facts_pack(_output(), _user(), retirement_age=55, retirement_modelled=False)

    block = facts["retirement"]
    assert "corpus_required_used" not in block
    assert "corpus_required_used_indian" not in block
    assert "corpus_required_pv_today" not in block
    assert block["planned_retirement_date_from_profile"] is not None, (
        "the profile date itself still matters — it sets the horizon"
    )
    assert block["is_funded_as_a_goal"] is False


def test_modelled_retirement_keeps_its_corpus_figures():
    facts = _build_facts_pack(_output(), _user(), retirement_age=55, retirement_modelled=True)

    block = facts["retirement"]
    assert block["corpus_required_used"] == 163_749_000
    assert block["is_funded_as_a_goal"] is True


@pytest.mark.parametrize("modelled", [True, False])
def test_the_horizon_facts_survive_either_way(modelled):
    block = _build_facts_pack(
        _output(), _user(), retirement_age=55, retirement_modelled=modelled
    )["retirement"]

    assert block["post_retirement_years"] == 30
    assert block["annual_household_expense_today_indian"] == "₹24 lakh"
