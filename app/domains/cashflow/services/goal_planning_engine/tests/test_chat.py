"""Chat-handler smoke test for goal_planning.

Mocks the service layer (covered by its own tests) and the formatter to
keep the test offline. Verifies the handler:
- registers under "goal_planning"
- routes through dispatch_chat
- propagates the formatter's text back
- returns the fallback text + an apology when DOB is missing
- returns an apology when financial profile is missing
"""

from __future__ import annotations

import uuid
from datetime import date
from types import SimpleNamespace

import pytest


@pytest.fixture(autouse=True)
def _stub_dependencies(monkeypatch):
    from app.domains.cashflow.services.goal_planning_engine import chat as gp_chat
    from app.domains.cashflow.services.goal_planning_engine.service import (
        GoalPlanningServiceOutcome,
    )

    async def fake_compute(
        *, user, user_question, chat_session_id, anchor_date, db=None
    ):
        if getattr(user, "date_of_birth", None) is None:
            raise ValueError("missing_date_of_birth")
        if getattr(user, "personal_finance_profile", None) is None:
            raise ValueError("missing_financial_profile")
        return GoalPlanningServiceOutcome(
            facts_pack={"headline": {"corpus_today_indian": "₹5 lakh"}},
            fallback_text="Stub fallback text.",
        )

    async def fake_formatter(**kwargs):
        return f"FORMATTED({kwargs['module_name']}/{kwargs['action_mode']}): ok"

    async def fake_relay(*, ctx, module_name, message, action_mode="redirect"):
        # mirror the helper: relayed text carries the canned message faithfully
        return message

    monkeypatch.setattr(gp_chat, "compute_goal_planning_snapshot", fake_compute)
    monkeypatch.setattr(gp_chat, "format_with_telemetry", fake_formatter)
    monkeypatch.setattr(gp_chat, "format_relay_or_canned", fake_relay)


def _ctx(user):
    return SimpleNamespace(
        user_ctx=user,
        user_question="Am I on track?",
        conversation_history=[],
        session_id=uuid.uuid4(),
        effective_user_id=uuid.uuid4(),
        db=None,
    )


@pytest.mark.asyncio
async def test_handler_routes_through_dispatcher():
    from app.domains.ai_engine.chat_dispatcher import dispatch_chat
    from app.domains.cashflow.services.goal_planning_engine import chat as _gp_chat  # noqa: F401

    user = SimpleNamespace(
        date_of_birth=date(1990, 1, 1),
        first_name="Asha",
        personal_finance_profile=SimpleNamespace(annual_income=1_000_000),
    )
    result = await dispatch_chat("goal_planning", _ctx(user))
    assert result.text.startswith("FORMATTED(goal_planning/narrate)")


@pytest.mark.asyncio
async def test_missing_dob_returns_apology_text():
    from app.domains.ai_engine.chat_dispatcher import dispatch_chat
    from app.domains.cashflow.services.goal_planning_engine import chat as _gp_chat  # noqa: F401

    user = SimpleNamespace(
        date_of_birth=None,
        first_name=None,
        personal_finance_profile=SimpleNamespace(annual_income=1_000_000),
    )
    result = await dispatch_chat("goal_planning", _ctx(user))
    assert "date of birth" in result.text.lower()


@pytest.mark.asyncio
async def test_missing_financial_profile_returns_apology():
    from app.domains.ai_engine.chat_dispatcher import dispatch_chat
    from app.domains.cashflow.services.goal_planning_engine import chat as _gp_chat  # noqa: F401

    user = SimpleNamespace(
        date_of_birth=date(1990, 1, 1),
        first_name=None,
        personal_finance_profile=None,
    )
    result = await dispatch_chat("goal_planning", _ctx(user))
    assert "financial profile" in result.text.lower()
