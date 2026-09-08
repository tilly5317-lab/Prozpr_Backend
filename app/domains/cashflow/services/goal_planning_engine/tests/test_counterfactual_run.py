"""A counterfactual run applies overrides and must NOT persist.

Every goal turn today writes ~30 rows (a plan run, headline, fund-flow summary
and one row per projection year) unconditionally. If "what if I retire at 50?"
persisted, it would overwrite the customer's real plan history — and the Goal
Planning screen reads the latest run. Asset allocation already draws this line:
``counterfactual_explore`` runs with overrides and does not persist, while
``recompute_full`` runs and does.
"""

from __future__ import annotations

import uuid
from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import app.domains.cashflow.services.goal_planning_engine.service as svc


@pytest.fixture
def harness(monkeypatch):
    """Stub everything except the code under test; capture engine input + persists."""
    from app.domains.ai_engine.common import ensure_ai_agents_path

    ensure_ai_agents_path()
    import cashflow_statement.engine as engine
    from app.domains.cashflow.services.goal_planning_engine import input_builder as ib
    import app.domains.portfolio.services.portfolio_service as ps

    seen: dict = {"persisted": False, "engine_input": None}

    from cashflow_statement.models import (
        ClientProfile,
        GoalPlanningInput,
        RetirementInput,
    )

    base = GoalPlanningInput(
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

    async def _no_portfolio(db, user_id):
        return None

    async def _persist(db, user_id, session_id, output):
        seen["persisted"] = True
        return uuid.uuid4()

    def _engine(inp):
        seen["engine_input"] = inp
        return SimpleNamespace()

    monkeypatch.setattr(
        ib, "build_goal_planning_input_for_user",
        lambda user, anchor, portfolio_value=None: (base, {}),
    )
    monkeypatch.setattr(ps, "get_primary_portfolio", _no_portfolio)
    monkeypatch.setattr(engine, "compute_full_projection", _engine)
    monkeypatch.setattr(svc, "_persist_plan_run", _persist)
    monkeypatch.setattr(svc, "log_trigger", lambda **kw: None)
    monkeypatch.setattr(svc, "log_output", lambda **kw: None)
    monkeypatch.setattr(svc, "_build_facts_pack", lambda output, user, **kw: {})
    monkeypatch.setattr(svc, "_build_fallback_text", lambda output: "")
    return seen


async def _run(overrides=None):
    return await svc.compute_goal_planning_snapshot(
        user=SimpleNamespace(id=uuid.uuid4(), first_name="X"),
        user_question="what if I retire at 50?",
        chat_session_id=str(uuid.uuid4()),
        anchor_date=date(2026, 7, 26),
        db=MagicMock(),
        overrides=overrides,
    )


@pytest.mark.asyncio
async def test_base_run_persists(harness):
    await _run(overrides=None)

    assert harness["persisted"] is True
    assert harness["engine_input"].retirement.retirement_age == 55


@pytest.mark.asyncio
async def test_counterfactual_applies_the_override(harness):
    await _run(overrides={"retirement_age": 50})

    assert harness["engine_input"].retirement.retirement_age == 50


@pytest.mark.asyncio
async def test_counterfactual_does_not_persist(harness):
    await _run(overrides={"retirement_age": 50})

    assert harness["persisted"] is False, (
        "a hypothetical must not overwrite the customer's real plan history"
    )
