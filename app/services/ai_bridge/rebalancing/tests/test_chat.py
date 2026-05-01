"""Mirror of asset_allocation's @register lock test, plus handler smoke tests."""

from __future__ import annotations

import asyncio
import datetime
import importlib
import unittest
import uuid
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.ai_bridge.chat_dispatcher import _HANDLERS
from app.services.ai_bridge.rebalancing import chat as mod
from app.services.chat_core.turn_context import AgentRunRecord, TurnContext


def _agent_run(payload: dict | None = None) -> AgentRunRecord:
    return AgentRunRecord(
        id=uuid.uuid4(),
        module="rebalancing",
        intent_detected="rebalancing",
        input_payload={},
        output_payload=payload or {"trades": []},
        created_at=datetime.datetime.utcnow(),
    )


def _ctx(question: str = "rebalance my portfolio", *, last_run: AgentRunRecord | None = None) -> TurnContext:
    last_runs = {"rebalancing": last_run} if last_run else {}
    return TurnContext(
        user_ctx=MagicMock(date_of_birth=date(1986, 1, 1), first_name="Tilly"),
        user_question=question,
        conversation_history=[],
        client_context=None,
        session_id=uuid.uuid4(),
        db=MagicMock(),
        effective_user_id=uuid.uuid4(),
        last_agent_runs=last_runs,
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
    assert result.chart is None


def test_handle_forwards_chart_payload_when_present(monkeypatch):
    """When the service picks a chart, the handler converts it to a JSON dict
    on ChatHandlerResult.chart so the brain → router → frontend chain can
    surface it without re-importing pydantic models downstream.
    """
    from app.services.ai_bridge.rebalancing import chat as rb_chat
    from app.services.ai_bridge.rebalancing.charts import ChartSpec
    from app.services.ai_bridge.rebalancing.service import RebalancingRunOutcome

    chart = ChartSpec(
        chart_type="category_gap_bar",
        title="Gap chart",
        caption="caption",
        data={"categories": ["Large Cap Fund"], "series": []},
    )
    fake = RebalancingRunOutcome(
        response=None,
        formatted_text="ok",
        recommendation_id=uuid.uuid4(),
        chart=chart,
    )
    monkeypatch.setattr(
        rb_chat, "compute_rebalancing_result",
        AsyncMock(return_value=fake),
    )
    result = asyncio.run(rb_chat.handle(_ctx()))
    assert isinstance(result.chart, dict)
    assert result.chart["chart_type"] == "category_gap_bar"
    assert result.chart["title"] == "Gap chart"
    assert result.chart["data"]["categories"] == ["Large Cap Fund"]


class DetectRebalActionTests(unittest.TestCase):

    def test_narrate_mode_for_explanation_question(self):
        with patch.object(mod, "_ainvoke",
                          new=AsyncMock(return_value=mod.RebalanceAction(mode="narrate"))):
            action = asyncio.run(mod._detect_rebal_action(_agent_run(), _ctx("why are you selling X?")))
        self.assertEqual(action.mode, "narrate")

    def test_recompute_mode_for_explicit_rerun(self):
        with patch.object(mod, "_ainvoke",
                          new=AsyncMock(return_value=mod.RebalanceAction(mode="recompute"))):
            action = asyncio.run(mod._detect_rebal_action(_agent_run(), _ctx("redo the trades")))
        self.assertEqual(action.mode, "recompute")

    def test_clarify_mode_carries_question(self):
        ret = mod.RebalanceAction(mode="clarify", clarification_question="Which fund?")
        with patch.object(mod, "_ainvoke", new=AsyncMock(return_value=ret)):
            action = asyncio.run(mod._detect_rebal_action(_agent_run(), _ctx("change something")))
        self.assertEqual(action.mode, "clarify")
        self.assertEqual(action.clarification_question, "Which fund?")

    def test_redirect_mode_carries_reason(self):
        ret = mod.RebalanceAction(mode="redirect", redirect_reason="lock fund Y")
        with patch.object(mod, "_ainvoke", new=AsyncMock(return_value=ret)):
            action = asyncio.run(mod._detect_rebal_action(_agent_run(), _ctx("keep fund Y")))
        self.assertEqual(action.mode, "redirect")
        self.assertIn("lock", action.redirect_reason)


if __name__ == "__main__":
    unittest.main()
