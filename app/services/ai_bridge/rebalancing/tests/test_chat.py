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
    monkeypatch.setattr(rb_chat, "build_rebal_facts_pack", lambda _: {})

    with patch("app.services.ai_bridge.answer_formatter.formatter.format_answer",
               new=AsyncMock(return_value="OK plan")), \
         patch("app.services.ai_bridge.answer_formatter.formatter.record_ai_module_run",
               new=AsyncMock(return_value=None)):
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
    monkeypatch.setattr(rb_chat, "build_rebal_facts_pack", lambda _: {})

    with patch("app.services.ai_bridge.answer_formatter.formatter.format_answer",
               new=AsyncMock(return_value="ok")), \
         patch("app.services.ai_bridge.answer_formatter.formatter.record_ai_module_run",
               new=AsyncMock(return_value=None)):
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


class HandleRoutingTests(unittest.TestCase):

    def test_first_turn_runs_engine_and_calls_formatter(self):
        outcome = MagicMock(
            response=MagicMock(),
            blocking_message=None,
            allocation_snapshot_id=uuid.uuid4(),
            recommendation_id=uuid.uuid4(),
            chart=None,
        )
        with patch.object(mod, "compute_rebalancing_result",
                          new=AsyncMock(return_value=outcome)), \
             patch("app.services.ai_bridge.answer_formatter.formatter.format_answer",
                   new=AsyncMock(return_value="tailored")), \
             patch("app.services.ai_bridge.rebalancing.chat.build_rebal_facts_pack",
                   return_value={}), \
             patch("app.services.ai_bridge.answer_formatter.formatter.record_ai_module_run",
                   new=AsyncMock(return_value=None)):
            result = asyncio.run(mod.handle(_ctx("rebalance my portfolio")))
        self.assertEqual(result.text, "tailored")

    def test_followup_clarify_bypasses_formatter(self):
        action = mod.RebalanceAction(mode="clarify", clarification_question="Which fund?")
        with patch.object(mod, "_detect_rebal_action",
                          new=AsyncMock(return_value=action)), \
             patch("app.services.ai_bridge.answer_formatter.formatter.format_answer",
                   new=AsyncMock()) as fmt:
            result = asyncio.run(mod.handle(_ctx("change something", last_run=_agent_run())))
        self.assertEqual(result.text, "Which fund?")
        fmt.assert_not_called()

    def test_followup_narrate_does_not_re_run_engine(self):
        action = mod.RebalanceAction(mode="narrate")
        with patch.object(mod, "_detect_rebal_action",
                          new=AsyncMock(return_value=action)), \
             patch.object(mod, "compute_rebalancing_result",
                          new=AsyncMock()) as engine, \
             patch.object(mod, "_rehydrate_response", return_value=MagicMock()), \
             patch("app.services.ai_bridge.answer_formatter.formatter.format_answer",
                   new=AsyncMock(return_value="explained")), \
             patch("app.services.ai_bridge.rebalancing.chat.build_rebal_facts_pack",
                   return_value={}), \
             patch("app.services.ai_bridge.answer_formatter.formatter.record_ai_module_run",
                   new=AsyncMock(return_value=None)):
            result = asyncio.run(mod.handle(_ctx("why?", last_run=_agent_run({"rebalancing_response": {"rows": []}}))))
        self.assertEqual(result.text, "explained")
        engine.assert_not_called()

    def test_followup_recompute_re_runs_engine(self):
        action = mod.RebalanceAction(mode="recompute")
        outcome = MagicMock(
            response=MagicMock(),
            blocking_message=None,
            allocation_snapshot_id=uuid.uuid4(),
            recommendation_id=uuid.uuid4(),
            chart=None,
        )
        with patch.object(mod, "_detect_rebal_action",
                          new=AsyncMock(return_value=action)), \
             patch.object(mod, "compute_rebalancing_result",
                          new=AsyncMock(return_value=outcome)), \
             patch("app.services.ai_bridge.answer_formatter.formatter.format_answer",
                   new=AsyncMock(return_value="redone")), \
             patch("app.services.ai_bridge.rebalancing.chat.build_rebal_facts_pack",
                   return_value={}), \
             patch("app.services.ai_bridge.answer_formatter.formatter.record_ai_module_run",
                   new=AsyncMock(return_value=None)):
            result = asyncio.run(mod.handle(_ctx("redo", last_run=_agent_run())))
        self.assertEqual(result.text, "redone")


class NarrateFallbackTests(unittest.TestCase):

    def test_narrate_returns_degraded_text_when_formatter_and_fallback_both_fail(self):
        from app.services.ai_bridge.answer_formatter import FormatterFailure
        action = mod.RebalanceAction(mode="narrate")
        # rehydrate returns a dict (validation drift), so fallback path picks
        # the degraded text. Then formatter raises FormatterFailure, so the
        # degraded text is what the user sees.
        with patch.object(mod, "_detect_rebal_action",
                          new=AsyncMock(return_value=action)), \
             patch.object(mod, "_rehydrate_response", return_value={"rows": []}), \
             patch("app.services.ai_bridge.answer_formatter.formatter.format_answer",
                   new=AsyncMock(side_effect=FormatterFailure("api_down"))), \
             patch("app.services.ai_bridge.answer_formatter.formatter.record_ai_module_run",
                   new=AsyncMock(return_value=None)):
            result = asyncio.run(mod.handle(_ctx("why?", last_run=_agent_run({"rebalancing_response": {"rows": []}}))))
        self.assertIn("redo the trades", result.text)


if __name__ == "__main__":
    unittest.main()
