"""End-to-end test of the goal_planning service (skips the live LLM).

Builds a synthetic User, runs ``compute_goal_planning_snapshot``, and verifies
the resulting ``facts_pack`` shape and the fallback-brief contract. The engine
is invoked for real (it's deterministic), but the summarizer LLM call is
monkey-patched so tests run offline. The DB interactions (_ensure_assumptions,
_persist_plan_run) are stubbed.
"""
from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.services.ai_bridge.goal_planning import service as svc
from app.services.ai_bridge.goal_planning.service import (
    GoalPlanningServiceOutcome,
)


def _pfp():
    return SimpleNamespace(
        annual_income=1_800_000,
        effective_tax_rate=0.18,
        financial_assets=1_500_000,
        financial_liabilities_excl_mortgage=200_000,
        monthly_household_expense=60_000,
        starting_monthly_investment=20_000,
    )


def _assumption_row():
    return SimpleNamespace(
        inflation_property=0.06,
        inflation_child_abroad_education=0.08,
        inflation_child_local_education=0.06,
        inflation_child_marriage=0.06,
        inflation_household_expense=0.06,
        inflation_post_retirement=0.06,
        annual_income_growth=0.08,
        annual_invested_amount_growth=0.08,
        roi_near_term_post_tax=0.05,
        roi_mid_term_post_tax=0.07,
        roi_long_term_post_tax=0.09,
        roi_retired_portfolio_annual=0.09,
        near_term_horizon_years=2,
        medium_term_horizon_years=3,
        default_mortgage_tenure_years=20,
        default_mortgage_interest_annual=0.075,
        default_property_downpayment_pct=0.20,
        default_sip_share=0.75,
    )


def _user_with_goals():
    """Synthetic profile with a custom goal."""
    return SimpleNamespace(
        id="00000000-0000-0000-0000-000000000001",
        date_of_birth=date(1994, 7, 22),
        assumed_lifespan_years=85,
        first_name="Test",
        personal_finance_profile=_pfp(),
        financial_goals=[
            SimpleNamespace(
                name="Education Fund",
                goal_name="Education Fund",
                goal_type_cashflow=SimpleNamespace(value="child_local_education"),
                goal_date=date(2038, 7, 1),
                goal_value_pv=3_000_000,
                goal_value_fv=None,
                target_pv=None,
                target_fv=None,
                is_downpayment_only=False,
                upfront_amount=None,
                downpayment_pct=None,
                inflation_annual=0.06,
                mortgage_tenure_years=None,
                mortgage_interest_annual=None,
                date_of_birth=None,
                present_value_amount=3_000_000,
            ),
        ],
        current_properties=[],
        cashflow_one_off_events=[],
        cashflow_assumption=None,
    )


@pytest.fixture(autouse=True)
def _stub_summarizer(monkeypatch):
    """Stub the summarizer LLM call."""
    from cashflow_statement.models import PlanSummary, GoalBullet

    def fake_summarize(output):
        return PlanSummary(
            top_line="Stub summary for testing.",
            retirement_note="Stub retirement note.",
            goals=[GoalBullet(
                name=g.name,
                verdict="unfunded" if g.shortfall_fv > 0 else "funded",
                headline_amount=f"₹{g.corpus_required_fv:,.0f}",
                note="stub",
            ) for g in output.goals],
            cashflow_note="Stub cashflow note.",
            risks=["stub risk"],
            next_steps=["Increase SIP by 10%"],
        )

    monkeypatch.setattr(
        "cashflow_statement.summarizer.summarize_plan", fake_summarize
    )


@pytest.fixture
def mock_db():
    """Mock AsyncSession that stubs the DB interactions."""
    db = AsyncMock()
    db.execute = AsyncMock(return_value=SimpleNamespace(
        scalar_one_or_none=lambda: None
    ))
    db.add = lambda x: None
    db.flush = AsyncMock()
    return db


@pytest.mark.asyncio
async def test_outcome_facts_pack_shape(mock_db):
    user = _user_with_goals()
    with patch(
        "app.services.ai_bridge.goal_planning.input_builder._ensure_assumptions",
        return_value=_assumption_row(),
    ):
        outcome = await svc.compute_goal_planning_snapshot(
            user=user,
            user_question="Am I on track for my education goal?",
            chat_session_id="test-session-1",
            anchor_date=date(2026, 5, 15),
            db=mock_db,
        )

    fp = outcome.facts_pack
    assert "headline" in fp
    assert "retirement" in fp
    assert "cashflow_horizon" in fp
    assert "goals" in fp
    assert "next_steps" in fp
    assert "narrative" in fp

    assert fp["headline"]["corpus_today_indian"] is not None
    assert fp["headline"]["is_feasible"] in (True, False)
    assert len(fp["goals"]) >= 1


@pytest.mark.asyncio
async def test_missing_dob_raises(mock_db):
    user = SimpleNamespace(
        id="00000000-0000-0000-0000-000000000002",
        date_of_birth=None,
        assumed_lifespan_years=None,
        personal_finance_profile=_pfp(),
        financial_goals=[],
        current_properties=[],
        cashflow_one_off_events=[],
        cashflow_assumption=None,
    )
    with patch(
        "app.services.ai_bridge.goal_planning.input_builder._ensure_assumptions",
        return_value=_assumption_row(),
    ):
        with pytest.raises(ValueError, match="missing_date_of_birth"):
            await svc.compute_goal_planning_snapshot(
                user=user, user_question="?",
                chat_session_id="x", anchor_date=date(2026, 5, 15),
                db=mock_db,
            )


@pytest.mark.asyncio
async def test_missing_financial_profile_raises(mock_db):
    user = SimpleNamespace(
        id="00000000-0000-0000-0000-000000000003",
        date_of_birth=date(1990, 1, 1),
        assumed_lifespan_years=85,
        personal_finance_profile=None,
        financial_goals=[],
        current_properties=[],
        cashflow_one_off_events=[],
        cashflow_assumption=None,
    )
    with patch(
        "app.services.ai_bridge.goal_planning.input_builder._ensure_assumptions",
        return_value=_assumption_row(),
    ):
        with pytest.raises(ValueError, match="missing_financial_profile"):
            await svc.compute_goal_planning_snapshot(
                user=user, user_question="?",
                chat_session_id="x", anchor_date=date(2026, 5, 15),
                db=mock_db,
            )
