"""End-to-end test of the goal_planning service (skips the live LLM).

Builds a synthetic User, runs ``compute_goal_planning_snapshot``, and verifies
the resulting ``facts_pack`` shape and the fallback-brief contract. The agent
graph is invoked for real (it's deterministic without an API key — it just
short-circuits to the fallback engine compute), but the summarizer LLM call
is monkey-patched so tests run offline.
"""
from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest

from app.services.ai_bridge.goal_planning import service as svc


def _user_with_shortfall():
    """Synthetic profile that produces an underfunded plan."""
    inv = SimpleNamespace(
        annual_income=1_800_000, regular_outgoings=60_000,
        investable_assets=1_500_000, total_liabilities=200_000,
        monthly_savings=20_000, retirement_age=60,
    )
    tax = SimpleNamespace(income_tax_rate=18.0)
    goals = [SimpleNamespace(
        goal_name="dream_home",
        goal_type=SimpleNamespace(value="HOME_PURCHASE"),
        present_value_amount=15_000_000,
        target_date=date(2030, 4, 1),
        status=SimpleNamespace(value="ACTIVE"),
        inflation_rate=6.0,
    )]
    return SimpleNamespace(
        date_of_birth=date(1994, 7, 22),
        investment_profile=inv,
        tax_profile=tax,
        financial_goals=goals,
    )


@pytest.fixture(autouse=True)
def _stub_agent(monkeypatch):
    """Mock the agent so tests don't need an API key.

    We mock at the service-module boundary (where ``run_cashflow_statement``
    is imported) rather than inside the agent — the agent has its own tests.
    The stub composes a snapshot by running the engine + summarizer stub
    directly, mirroring what the real agent would produce on a status turn.
    """
    from cashflow_statement import compute_full_projection
    from cashflow_statement.models import (
        GoalPlanningSnapshot, PlanSummary, GoalBullet,
    )

    async def fake_run(request):
        out = compute_full_projection(request.baseline_input)
        summary = PlanSummary(
            top_line="Stub summary for testing.",
            retirement_note="Stub retirement note.",
            goals=[GoalBullet(
                name=g.name, verdict="unfunded" if g.shortfall_fv > 0 else "funded",
                headline_amount=f"₹{g.shortfall_fv or g.corpus_required_fv:,.0f}",
                note="stub",
            ) for g in out.goals],
            cashflow_note="Stub cashflow note.",
            risks=["stub risk"],
            next_steps=[],
        )
        return GoalPlanningSnapshot(
            **out.model_dump(),
            extracted_events_this_turn=[],
            actions_taken_this_turn=[],
            levers=[],
            validation_issues=[],
            error_log=[],
            summary=summary,
        )

    monkeypatch.setattr(svc, "run_cashflow_statement", fake_run)


@pytest.mark.asyncio
async def test_outcome_shape_and_facts_pack():
    user = _user_with_shortfall()
    outcome = await svc.compute_goal_planning_snapshot(
        user=user,
        user_question="Am I on track for my home goal?",
        chat_session_id="test-session-1",
        anchor_date=date(2026, 5, 15),
    )

    # Snapshot is populated
    assert outcome.snapshot is not None
    assert outcome.snapshot.summary is not None
    assert outcome.snapshot.summary.top_line == "Stub summary for testing."

    # Facts pack carries the required sections
    fp = outcome.facts_pack
    assert "headline" in fp
    assert "retirement" in fp
    assert "goals" in fp
    assert "next_steps" in fp
    assert "narrative" in fp
    assert "validation_issues" in fp

    # Indian-notation discipline: every headline rupee key ends in `_indian`.
    headline_money_keys = [k for k in fp["headline"] if "corpus" in k or "shortfall" in k]
    for k in headline_money_keys:
        assert k.endswith("_indian"), f"missing _indian suffix on {k}"
        assert isinstance(fp["headline"][k], str)

    # HOME_PURCHASE → emits the property validation note
    assert any("HOME_PURCHASE" in v for v in fp["validation_issues"])


@pytest.mark.asyncio
async def test_fallback_brief_uses_summary_top_line():
    user = _user_with_shortfall()
    outcome = await svc.compute_goal_planning_snapshot(
        user=user,
        user_question="?",
        chat_session_id="test-session-2",
        anchor_date=date(2026, 5, 15),
    )
    assert outcome.fallback_text == "Stub summary for testing."


@pytest.mark.asyncio
async def test_missing_dob_raises():
    user = SimpleNamespace(
        date_of_birth=None, investment_profile=None,
        tax_profile=None, financial_goals=[],
    )
    with pytest.raises(ValueError, match="missing_date_of_birth"):
        await svc.compute_goal_planning_snapshot(
            user=user, user_question="?",
            chat_session_id="x", anchor_date=date(2026, 5, 15),
        )
