"""asset_allocation_chat: unified handler with all 7 modes."""

from __future__ import annotations

import asyncio
import unittest
import uuid
from datetime import date, datetime
from typing import Any
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


def _ctx(
    question: str,
    *,
    last_alloc: AgentRunRecord | None = None,
    awaiting_save: bool = False,
) -> TurnContext:
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
        awaiting_save=awaiting_save,
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
             patch("app.services.ai_bridge.answer_formatter.formatter.format_answer",
                   new=AsyncMock(return_value="brief text")), \
             patch("app.services.ai_bridge.answer_formatter.formatter.record_ai_module_run",
                   new=AsyncMock(return_value=None)):
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
             patch("app.services.ai_bridge.answer_formatter.formatter.format_answer",
                   new=AsyncMock(return_value="narration text")), \
             patch("app.services.ai_bridge.answer_formatter.formatter.record_ai_module_run",
                   new=AsyncMock(return_value=None)), \
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
             patch("app.services.ai_bridge.answer_formatter.formatter.format_answer",
                   new=AsyncMock(return_value="educational text")), \
             patch("app.services.ai_bridge.answer_formatter.formatter.record_ai_module_run",
                   new=AsyncMock(return_value=None)), \
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
            chat_ctx = kwargs.get("chat_ctx")
            captured["chat_ctx_overrides"] = chat_ctx.chat_overrides if chat_ctx else None
            return _engine_outcome_with_ids()

        action = mod.ChatAction(mode="counterfactual_explore",
                                 overrides={"effective_risk_score": 7.0})
        with patch.object(mod, "_detect_action",
                          new=AsyncMock(return_value=action)), \
             patch.object(mod, "compute_allocation_result", side_effect=fake_compute), \
             patch.object(mod, "build_aa_facts_pack", return_value={}), \
             patch.object(mod, "upsert_awaiting_save", new=AsyncMock()), \
             patch("app.services.ai_bridge.answer_formatter.formatter.format_answer",
                   new=AsyncMock(return_value="hypothetical text")), \
             patch("app.services.ai_bridge.answer_formatter.formatter.record_ai_module_run",
                   new=AsyncMock(return_value=None)):
            result = asyncio.run(mod.handle(_ctx("what if risk were 7?", last_alloc=_agent_run())))

        self.assertEqual(result.text, "hypothetical text")
        self.assertFalse(captured["persist"])
        self.assertIsNone(captured["db"])
        # Override flows via TurnContext.chat_overrides (NOT via setattr on User):
        self.assertEqual(captured["chat_ctx_overrides"], {"effective_risk_score": 7.0})
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
             patch("app.services.ai_bridge.answer_formatter.formatter.format_answer",
                   new=AsyncMock(return_value="updated brief")), \
             patch("app.services.ai_bridge.answer_formatter.formatter.record_ai_module_run",
                   new=AsyncMock(return_value=None)):
            ctx = _ctx("redo my plan", last_alloc=_agent_run())
            result = asyncio.run(mod.handle(ctx))

        self.assertTrue(captured["persist"])
        self.assertIsNotNone(captured["db"])
        self.assertIsNotNone(result.snapshot_id)
        self.assertIsNotNone(result.rebalancing_recommendation_id)


class SaveLastCounterfactualTests(unittest.TestCase):

    def test_save_last_counterfactual_persists_with_loaded_overrides(self):
        """save_last_counterfactual loads overrides from chat_ai_module_runs and re-runs with persist=True."""
        captured = {}

        async def fake_compute(user, question, **kwargs):
            captured["persist"] = kwargs.get("persist_recommendation")
            chat_ctx = kwargs.get("chat_ctx")
            captured["chat_ctx_overrides"] = chat_ctx.chat_overrides if chat_ctx else None
            return _engine_outcome_with_ids()

        action = mod.ChatAction(mode="save_last_counterfactual")
        upsert_mock = AsyncMock()
        with patch.object(mod, "_detect_action",
                          new=AsyncMock(return_value=action)), \
             patch.object(mod, "_load_last_counterfactual_overrides",
                          new=AsyncMock(return_value={"effective_risk_score": 7.0})), \
             patch.object(mod, "compute_allocation_result", side_effect=fake_compute), \
             patch.object(mod, "build_aa_facts_pack", return_value={}), \
             patch.object(mod, "upsert_awaiting_save", new=upsert_mock), \
             patch("app.services.ai_bridge.answer_formatter.formatter.format_answer",
                   new=AsyncMock(return_value="Saved. Your plan now has risk 7.")), \
             patch("app.services.ai_bridge.answer_formatter.formatter.record_ai_module_run",
                   new=AsyncMock(return_value=None)):
            result = asyncio.run(mod.handle(
                _ctx("save it", last_alloc=_agent_run(), awaiting_save=True),
            ))

        self.assertTrue(captured["persist"])
        # Override flows via TurnContext.chat_overrides (NOT via setattr on User):
        self.assertEqual(captured["chat_ctx_overrides"], {"effective_risk_score": 7.0})
        self.assertIsNotNone(result.snapshot_id)
        # State machine: awaiting_save reset to False after successful save.
        upsert_call = upsert_mock.call_args
        self.assertIsNotNone(upsert_call, "upsert_awaiting_save not called after save")
        self.assertEqual(upsert_call.args[2], False)

    def test_save_with_no_prior_counterfactual_responds_gracefully(self):
        """save_last_counterfactual with no recent counterfactual returns guidance."""
        action = mod.ChatAction(mode="save_last_counterfactual")
        with patch.object(mod, "_detect_action",
                          new=AsyncMock(return_value=action)), \
             patch.object(mod, "_load_last_counterfactual_overrides",
                          new=AsyncMock(return_value=None)), \
             patch.object(mod, "compute_allocation_result",
                          new=AsyncMock()) as engine:
            result = asyncio.run(mod.handle(_ctx("save it", last_alloc=_agent_run())))
        self.assertIn("no recent 'what if'", result.text)
        engine.assert_not_called()


class CounterfactualCapturesOverridesTests(unittest.TestCase):

    def test_counterfactual_writes_overrides_for_save(self):
        """counterfactual_explore writes a chat_ai_module_runs row capturing overrides."""
        captured_records: list[dict] = []

        async def fake_record(_db, **kwargs):
            captured_records.append(kwargs)
            return None

        action = mod.ChatAction(mode="counterfactual_explore",
                                 overrides={"effective_risk_score": 7.0})
        upsert_mock = AsyncMock()
        with patch.object(mod, "_detect_action",
                          new=AsyncMock(return_value=action)), \
             patch.object(mod, "compute_allocation_result",
                          new=AsyncMock(return_value=_engine_outcome_with_ids())), \
             patch.object(mod, "build_aa_facts_pack", return_value={}), \
             patch.object(mod, "upsert_awaiting_save", new=upsert_mock), \
             patch("app.services.ai_module_telemetry.record_ai_module_run",
                   side_effect=fake_record), \
             patch("app.services.ai_bridge.answer_formatter.formatter.format_answer",
                   new=AsyncMock(return_value="hypothetical")), \
             patch("app.services.ai_bridge.answer_formatter.formatter.record_ai_module_run",
                   new=AsyncMock(return_value=None)):
            asyncio.run(mod.handle(_ctx("what if risk 7?", last_alloc=_agent_run())))

        # At least one record_ai_module_run call from chat.py with our reason
        chat_records = [r for r in captured_records if r.get("reason") == "counterfactual_overrides"]
        self.assertGreaterEqual(len(chat_records), 1)
        rec = chat_records[0]
        self.assertEqual(rec.get("module"), "asset_allocation")
        self.assertEqual(
            (rec.get("input_payload") or {}).get("overrides"),
            {"effective_risk_score": 7.0},
        )
        # State machine: awaiting_save flipped to True after successful counterfactual.
        upsert_call = upsert_mock.call_args
        self.assertIsNotNone(upsert_call, "upsert_awaiting_save not called after counterfactual")
        self.assertEqual(upsert_call.args[2], True)


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
             patch("app.services.ai_bridge.answer_formatter.formatter.format_answer",
                   new=AsyncMock(side_effect=FormatterFailure("boom"))), \
             patch.object(mod, "build_fallback_brief",
                          return_value="fallback brief text"), \
             patch("app.services.ai_bridge.answer_formatter.formatter.record_ai_module_run",
                   new=AsyncMock(return_value=None)):
            result = asyncio.run(mod.handle(_ctx("plan my retirement")))

        self.assertEqual(result.text, "fallback brief text")


class RehydrateFallbackTests(unittest.TestCase):

    def test_rehydration_failure_returns_degraded_text(self):
        action = mod.ChatAction(mode="narrate")
        with patch.object(mod, "_detect_action",
                          new=AsyncMock(return_value=action)), \
             patch.object(mod, "_rehydrate_last_alloc_output",
                          side_effect=ValueError("schema drift")), \
             patch("app.services.ai_bridge.answer_formatter.formatter.format_answer",
                   new=AsyncMock()) as fmt:
            result = asyncio.run(mod.handle(_ctx("explain my mix", last_alloc=_agent_run())))
        self.assertIn("redo the plan", result.text)
        fmt.assert_not_called()


class FormatterTelemetryTests(unittest.TestCase):

    def test_first_turn_records_formatter_columns_on_success(self):
        outcome = _engine_outcome_with_ids()
        captured: dict[str, Any] = {}

        async def fake_record(*args, **kwargs):
            captured.update(kwargs)
            return uuid.uuid4()

        with patch.object(mod, "compute_allocation_result",
                          new=AsyncMock(return_value=outcome)), \
             patch.object(mod, "build_aa_facts_pack", return_value={}), \
             patch("app.services.ai_bridge.answer_formatter.formatter.format_answer",
                   new=AsyncMock(return_value="tailored answer")), \
             patch("app.services.ai_bridge.answer_formatter.formatter.record_ai_module_run",
                   side_effect=fake_record):
            asyncio.run(mod.handle(_ctx("plan my retirement")))

        self.assertEqual(captured.get("action_mode"), "compute")
        self.assertTrue(captured.get("formatter_invoked"))
        self.assertTrue(captured.get("formatter_succeeded"))
        self.assertIsNone(captured.get("formatter_error_class"))
        self.assertIsNotNone(captured.get("formatter_latency_ms"))

    def test_first_turn_records_formatter_columns_on_failure(self):
        from app.services.ai_bridge.answer_formatter import FormatterFailure
        outcome = _engine_outcome_with_ids()
        captured: dict[str, Any] = {}

        async def fake_record(*args, **kwargs):
            captured.update(kwargs)
            return uuid.uuid4()

        with patch.object(mod, "compute_allocation_result",
                          new=AsyncMock(return_value=outcome)), \
             patch.object(mod, "build_aa_facts_pack", return_value={}), \
             patch("app.services.ai_bridge.answer_formatter.formatter.format_answer",
                   new=AsyncMock(side_effect=FormatterFailure("api_down"))), \
             patch.object(mod, "build_fallback_brief",
                          return_value="fallback"), \
             patch("app.services.ai_bridge.answer_formatter.formatter.record_ai_module_run",
                   side_effect=fake_record):
            asyncio.run(mod.handle(_ctx("plan my retirement")))

        self.assertEqual(captured.get("action_mode"), "compute")
        self.assertTrue(captured.get("formatter_invoked"))
        self.assertFalse(captured.get("formatter_succeeded"))
        self.assertEqual(captured.get("formatter_error_class"), "FormatterFailure")


if __name__ == "__main__":
    unittest.main()
