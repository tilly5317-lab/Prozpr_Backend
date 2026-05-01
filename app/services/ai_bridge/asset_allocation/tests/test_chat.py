"""asset_allocation_chat: unified handler with all 7 modes."""

from __future__ import annotations

import asyncio
import unittest
import uuid
from datetime import date, datetime
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.ai_bridge.asset_allocation import chat as mod
from app.services.ai_bridge.chat_dispatcher import ChatHandlerResult
from app.services.chat_core.turn_context import AgentRunRecord, TurnContext


def _agent_run() -> AgentRunRecord:
    return AgentRunRecord(
        id=uuid.uuid4(),
        module="asset_allocation",
        intent_detected="asset_allocation",
        input_payload={
            "effective_risk_score": 5.4, "age": 39, "annual_income": 1_000_000,
            "osi": 0.3, "savings_rate_adjustment": "none", "gap_exceeds_3": False,
            "total_corpus": 8_000_000, "monthly_household_expense": 50_000,
            "tax_regime": "new", "effective_tax_rate": 30.0, "goals": [],
        },
        output_payload={
            "allocation_result": {
                "grand_total": 8_000_000,
                "asset_class_breakdown": {
                    "actual": {
                        "equity_total_pct": 40.2,
                        "debt_total_pct": 51.0,
                        "others_total_pct": 8.8,
                    },
                },
            },
            "correlation_ids": {"snapshot_id": str(uuid.uuid4()),
                                "rebalancing_recommendation_id": str(uuid.uuid4())},
        },
        created_at=datetime.utcnow(),
    )


def _ctx(question: str, *, last_alloc: AgentRunRecord | None = None) -> TurnContext:
    last_runs = {"asset_allocation": last_alloc} if last_alloc else {}
    return TurnContext(
        user_ctx=MagicMock(date_of_birth=date(1986, 1, 1), first_name="Tilly"),
        user_question=question,
        conversation_history=[],
        client_context=None,
        session_id=uuid.uuid4(),
        db=MagicMock(),
        effective_user_id=uuid.uuid4(),
        last_agent_runs=last_runs,
        active_intent="asset_allocation",
    )


def _engine_outcome_with_ids(snap_id=None, rec_id=None):
    """Build a stub AllocationRunOutcome that compute_allocation_result might return."""
    outcome = MagicMock()
    outcome.result = MagicMock()
    outcome.result.grand_total = 8_000_000
    outcome.result.client_summary = MagicMock(
        effective_risk_score=5.4, age=39, goals=[]
    )
    outcome.result.bucket_allocations = []
    outcome.result.asset_class_breakdown = None
    outcome.result.aggregated_subgroups = []
    outcome.result.future_investments_summary = []
    outcome.result.model_dump = MagicMock(return_value={"grand_total": 8_000_000})
    outcome.blocking_message = None
    outcome.allocation_snapshot_id = snap_id or uuid.uuid4()
    outcome.rebalancing_recommendation_id = rec_id or uuid.uuid4()
    return outcome


class FirstTurnTests(unittest.TestCase):

    def test_first_turn_runs_engine_and_returns_ids(self):
        outcome = _engine_outcome_with_ids()

        with patch.object(mod, "compute_allocation_result",
                          new=AsyncMock(return_value=outcome)), \
             patch.object(mod, "build_aa_facts_pack", return_value={}), \
             patch("app.services.ai_bridge.asset_allocation.chat.format_answer",
                   new=AsyncMock(return_value="brief text")):
            result = asyncio.run(mod.handle(_ctx("plan my retirement")))

        self.assertIsInstance(result, ChatHandlerResult)
        self.assertEqual(result.text, "brief text")
        self.assertEqual(result.snapshot_id, outcome.allocation_snapshot_id)
        self.assertEqual(result.rebalancing_recommendation_id,
                         outcome.rebalancing_recommendation_id)

    def test_first_turn_blocking_message_passes_through(self):
        outcome = MagicMock()
        outcome.result = None
        outcome.blocking_message = "I need your date of birth..."
        outcome.allocation_snapshot_id = None
        outcome.rebalancing_recommendation_id = None

        with patch.object(mod, "compute_allocation_result",
                          new=AsyncMock(return_value=outcome)):
            result = asyncio.run(mod.handle(_ctx("plan my retirement")))

        self.assertEqual(result.text, "I need your date of birth...")
        self.assertIsNone(result.snapshot_id)


class NarrateModeTests(unittest.TestCase):

    def test_narrate_returns_text_no_engine(self):
        action = mod.ChatAction(mode="narrate")
        with patch.object(mod, "_detect_action",
                          new=AsyncMock(return_value=action)), \
             patch.object(mod, "_rehydrate_last_alloc_output", return_value=MagicMock()), \
             patch.object(mod, "build_aa_facts_pack", return_value={}), \
             patch("app.services.ai_bridge.asset_allocation.chat.format_answer",
                   new=AsyncMock(return_value="narration text")), \
             patch.object(mod, "compute_allocation_result",
                          new=AsyncMock()) as engine:
            result = asyncio.run(mod.handle(_ctx("is this too aggressive?", last_alloc=_agent_run())))

        self.assertEqual(result.text, "narration text")
        engine.assert_not_called()


class EducateModeTests(unittest.TestCase):

    def test_educate_returns_text_no_engine(self):
        action = mod.ChatAction(mode="educate")
        with patch.object(mod, "_detect_action",
                          new=AsyncMock(return_value=action)), \
             patch.object(mod, "_rehydrate_last_alloc_output", return_value=MagicMock()), \
             patch.object(mod, "build_aa_facts_pack", return_value={}), \
             patch("app.services.ai_bridge.asset_allocation.chat.format_answer",
                   new=AsyncMock(return_value="educational text")), \
             patch.object(mod, "compute_allocation_result",
                          new=AsyncMock()) as engine:
            result = asyncio.run(mod.handle(_ctx("what does multi-cap mean?", last_alloc=_agent_run())))

        self.assertEqual(result.text, "educational text")
        engine.assert_not_called()


class CounterfactualExploreTests(unittest.TestCase):

    def test_counterfactual_explore_runs_engine_no_persist(self):
        captured = {}

        async def fake_compute(user, question, **kwargs):
            captured["persist"] = kwargs.get("persist_recommendation")
            captured["db"] = kwargs.get("db")
            captured["risk_override_seen"] = getattr(user, "_chat_risk_score_override", None)
            return _engine_outcome_with_ids()

        action = mod.ChatAction(mode="counterfactual_explore",
                                 overrides={"effective_risk_score": 7.0})
        with patch.object(mod, "_detect_action",
                          new=AsyncMock(return_value=action)), \
             patch.object(mod, "compute_allocation_result", side_effect=fake_compute), \
             patch.object(mod, "build_aa_facts_pack", return_value={}), \
             patch("app.services.ai_bridge.asset_allocation.chat.format_answer",
                   new=AsyncMock(return_value="hypothetical text")):
            result = asyncio.run(mod.handle(_ctx("what if risk were 7?", last_alloc=_agent_run())))

        self.assertEqual(result.text, "hypothetical text")
        self.assertFalse(captured["persist"])
        self.assertIsNone(captured["db"])
        self.assertEqual(captured["risk_override_seen"], 7.0)
        self.assertIsNone(result.snapshot_id)

    def test_counterfactual_with_invalid_override_falls_to_redirect(self):
        action = mod.ChatAction(mode="counterfactual_explore",
                                 overrides={"unknown_key": 1.0})
        with patch.object(mod, "_detect_action",
                          new=AsyncMock(return_value=action)):
            result = asyncio.run(mod.handle(_ctx("what if?", last_alloc=_agent_run())))
        # Falls through to either redirect or invalid-override template; both mention Profile
        self.assertIn("Profile", result.text)


class ClarifyModeTests(unittest.TestCase):

    def test_clarify_returns_composed_question(self):
        action = mod.ChatAction(mode="clarify",
                                 clarification_question="What risk score?")
        with patch.object(mod, "_detect_action",
                          new=AsyncMock(return_value=action)):
            result = asyncio.run(mod.handle(_ctx("I want more risk", last_alloc=_agent_run())))
        self.assertEqual(result.text, "What risk score?")
        self.assertIsNone(result.snapshot_id)

    def test_clarify_without_question_uses_fallback(self):
        action = mod.ChatAction(mode="clarify", clarification_question=None)
        with patch.object(mod, "_detect_action",
                          new=AsyncMock(return_value=action)):
            result = asyncio.run(mod.handle(_ctx("I want something", last_alloc=_agent_run())))
        self.assertTrue(result.text)


class RecomputeFullTests(unittest.TestCase):

    def test_recompute_full_runs_engine_and_persists(self):
        captured = {}

        async def fake_compute(user, question, **kwargs):
            captured["persist"] = kwargs.get("persist_recommendation")
            captured["db"] = kwargs.get("db")
            return _engine_outcome_with_ids()

        action = mod.ChatAction(mode="recompute_full")
        with patch.object(mod, "_detect_action",
                          new=AsyncMock(return_value=action)), \
             patch.object(mod, "compute_allocation_result", side_effect=fake_compute), \
             patch.object(mod, "build_aa_facts_pack", return_value={}), \
             patch("app.services.ai_bridge.asset_allocation.chat.format_answer",
                   new=AsyncMock(return_value="updated brief")):
            ctx = _ctx("redo my plan", last_alloc=_agent_run())
            result = asyncio.run(mod.handle(ctx))

        self.assertTrue(captured["persist"])
        self.assertIsNotNone(captured["db"])
        self.assertIsNotNone(result.snapshot_id)
        self.assertIsNotNone(result.rebalancing_recommendation_id)


class RecomputeWithOverridesTests(unittest.TestCase):

    def test_recompute_with_overrides_persists_with_override_applied(self):
        captured = {}

        async def fake_compute(user, question, **kwargs):
            captured["persist"] = kwargs.get("persist_recommendation")
            captured["risk_override_seen"] = getattr(user, "_chat_risk_score_override", None)
            return _engine_outcome_with_ids()

        action = mod.ChatAction(mode="recompute_with_overrides",
                                 overrides={"effective_risk_score": 7.0})
        with patch.object(mod, "_detect_action",
                          new=AsyncMock(return_value=action)), \
             patch.object(mod, "compute_allocation_result", side_effect=fake_compute), \
             patch.object(mod, "build_aa_facts_pack", return_value={}), \
             patch("app.services.ai_bridge.asset_allocation.chat.format_answer",
                   new=AsyncMock(return_value="updated brief")):
            result = asyncio.run(mod.handle(_ctx("lock in risk 7", last_alloc=_agent_run())))

        self.assertTrue(captured["persist"])
        self.assertEqual(captured["risk_override_seen"], 7.0)
        self.assertIsNotNone(result.snapshot_id)


class RedirectModeTests(unittest.TestCase):

    def test_redirect_returns_template_with_reason(self):
        action = mod.ChatAction(mode="redirect", redirect_reason="change holdings")
        with patch.object(mod, "_detect_action",
                          new=AsyncMock(return_value=action)):
            result = asyncio.run(mod.handle(_ctx("swap arbitrage", last_alloc=_agent_run())))
        self.assertIn("Profile", result.text)
        self.assertIn("change holdings", result.text)


class DetectActionFailureTests(unittest.TestCase):

    def test_detect_action_failure_returns_degraded_text(self):
        with patch.object(mod, "_detect_action",
                          new=AsyncMock(side_effect=RuntimeError("LLM down"))):
            result = asyncio.run(mod.handle(_ctx("what?", last_alloc=_agent_run())))
        self.assertIn("rephrase", result.text)


class FallbackTests(unittest.TestCase):

    def test_first_turn_falls_back_to_brief_on_formatter_failure(self):
        outcome = _engine_outcome_with_ids()
        from app.services.ai_bridge.answer_formatter import FormatterFailure

        with patch.object(mod, "compute_allocation_result",
                          new=AsyncMock(return_value=outcome)), \
             patch.object(mod, "build_aa_facts_pack", return_value={}), \
             patch("app.services.ai_bridge.asset_allocation.chat.format_answer",
                   new=AsyncMock(side_effect=FormatterFailure("boom"))), \
             patch("app.services.ai_bridge.asset_allocation.chat.build_fallback_brief",
                   return_value="fallback brief text"):
            result = asyncio.run(mod.handle(_ctx("plan my retirement")))

        self.assertEqual(result.text, "fallback brief text")


class RehydrateFallbackTests(unittest.TestCase):

    def test_rehydration_failure_returns_degraded_text(self):
        action = mod.ChatAction(mode="narrate")
        with patch.object(mod, "_detect_action",
                          new=AsyncMock(return_value=action)), \
             patch.object(mod, "_rehydrate_last_alloc_output",
                          side_effect=ValueError("schema drift")), \
             patch("app.services.ai_bridge.asset_allocation.chat.format_answer",
                   new=AsyncMock()) as fmt:
            result = asyncio.run(mod.handle(_ctx("explain my mix", last_alloc=_agent_run())))
        self.assertIn("redo the plan", result.text)
        fmt.assert_not_called()


if __name__ == "__main__":
    unittest.main()
