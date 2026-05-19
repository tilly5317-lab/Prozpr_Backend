"""Chat-handler smoke test for goal_planning.

Mocks the service layer (covered by its own tests) and the formatter to
keep the test offline. Verifies the handler:
- registers under "goal_planning"
- routes through dispatch_chat
- propagates the formatter's text back
- returns the fallback text + an apology when DOB is missing
"""
from __future__ import annotations

import uuid
from datetime import date
from types import SimpleNamespace

import pytest


@pytest.fixture(autouse=True)
def _stub_dependencies(monkeypatch):
    # Importing the chat module triggers @register("goal_planning") side-effect.
    from app.services.ai_bridge.goal_planning import chat as gp_chat
    from app.services.ai_bridge.goal_planning import service as gp_svc

    async def fake_compute(*, user, user_question, chat_session_id, anchor_date):
        if getattr(user, "date_of_birth", None) is None:
            raise ValueError("missing_date_of_birth")
        return SimpleNamespace(
            snapshot=SimpleNamespace(summary=None),
            facts_pack={"headline": {"corpus_today_indian": "₹5 lakh"}},
            fallback_text="Stub fallback text.",
            validation_issues=[],
            defaults_applied=[],
        )

    async def fake_formatter(**kwargs):
        return f"FORMATTED({kwargs['module_name']}/{kwargs['action_mode']}): ok"

    monkeypatch.setattr(gp_chat, "compute_goal_planning_snapshot", fake_compute)
    monkeypatch.setattr(gp_chat, "format_with_telemetry", fake_formatter)
    # Also patch the service's symbol so callers that don't reuse gp_chat work.
    monkeypatch.setattr(gp_svc, "run_cashflow_statement", lambda *_a, **_k: None)


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
    from app.services.ai_bridge.chat_dispatcher import dispatch_chat
    # Trigger @register side-effect.
    from app.services.ai_bridge.goal_planning import chat as _gp_chat  # noqa: F401

    user = SimpleNamespace(
        date_of_birth=date(1990, 1, 1), first_name="Asha",
    )
    result = await dispatch_chat("goal_planning", _ctx(user))
    assert result.text.startswith("FORMATTED(goal_planning/narrate)")


@pytest.mark.asyncio
async def test_missing_dob_returns_apology_text():
    from app.services.ai_bridge.chat_dispatcher import dispatch_chat
    from app.services.ai_bridge.goal_planning import chat as _gp_chat  # noqa: F401

    user = SimpleNamespace(date_of_birth=None, first_name=None)
    result = await dispatch_chat("goal_planning", _ctx(user))
    assert "date of birth" in result.text.lower()
