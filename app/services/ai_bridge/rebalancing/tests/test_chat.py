"""Mirror of asset_allocation's @register lock test, plus handler smoke tests."""

from __future__ import annotations

import asyncio
import importlib
import uuid
from datetime import date
from unittest.mock import AsyncMock, MagicMock

from app.services.ai_bridge.chat_dispatcher import _HANDLERS
from app.services.chat_core.turn_context import TurnContext


def _ctx(question: str = "rebalance my portfolio") -> TurnContext:
    return TurnContext(
        user_ctx=MagicMock(date_of_birth=date(1986, 1, 1), id=uuid.uuid4()),
        user_question=question,
        conversation_history=[],
        client_context=None,
        session_id=uuid.uuid4(),
        db=MagicMock(),
        effective_user_id=uuid.uuid4(),
        last_agent_runs={},
        active_intent="rebalancing",
    )


def test_register_side_effect_for_rebalancing():
    """Importing rebalancing.chat must register the 'rebalancing' handler."""
    import app.services.ai_bridge.rebalancing.chat as mod

    importlib.reload(mod)
    assert "rebalancing" in _HANDLERS, (
        "@register('rebalancing') side-effect missing"
    )


def test_handle_returns_chat_handler_result_on_success(monkeypatch):
    from app.services.ai_bridge.rebalancing import chat as rb_chat
    from app.services.ai_bridge.rebalancing.service import RebalancingRunOutcome

    rec_id = uuid.uuid4()
    fake_outcome = RebalancingRunOutcome(
        response=None,
        formatted_text="OK plan",
        blocking_message=None,
        recommendation_id=rec_id,
        allocation_snapshot_id=None,
        used_cached_allocation=True,
    )
    monkeypatch.setattr(
        rb_chat,
        "compute_rebalancing_result",
        AsyncMock(return_value=fake_outcome),
    )

    result = asyncio.run(rb_chat.handle(_ctx()))
    assert result.text == "OK plan"
    assert result.rebalancing_recommendation_id == rec_id


def test_handle_returns_blocking_message(monkeypatch):
    from app.services.ai_bridge.rebalancing import chat as rb_chat
    from app.services.ai_bridge.rebalancing.service import RebalancingRunOutcome

    blocked = RebalancingRunOutcome(response=None, blocking_message="No DOB")
    monkeypatch.setattr(
        rb_chat,
        "compute_rebalancing_result",
        AsyncMock(return_value=blocked),
    )
    result = asyncio.run(rb_chat.handle(_ctx()))
    assert result.text == "No DOB"
    assert result.rebalancing_recommendation_id is None
