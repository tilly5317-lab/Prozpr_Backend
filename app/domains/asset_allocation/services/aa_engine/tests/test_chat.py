"""asset_allocation_chat: unified handler with all 6 modes."""

from __future__ import annotations

import asyncio
import unittest
import uuid
from datetime import date, datetime
from unittest.mock import AsyncMock, MagicMock, patch

from app.domains.asset_allocation.services.aa_engine import chat as mod
from app.domains.ai_engine.chat_dispatcher import ChatHandlerResult
from app.domains.ai_engine.turn_context import AgentRunRecord, TurnContext


def _agent_run() -> AgentRunRecord:
    return AgentRunRecord(
        id=uuid.uuid4(),
        module="asset_allocation",
        intent_detected="asset_allocation",
        input_payload={
            "effective_risk_score": 5.4,
            "age": 39,
            "annual_income": 1_000_000,
            "osi": 0.3,
            "savings_rate_adjustment": "none",
            "gap_exceeds_3": False,
            "total_corpus": 8_000_000,
            "monthly_household_expense": 50_000,
            "tax_regime": "new",
            "effective_tax_rate": 30.0,
            "goals": [],
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
            "correlation_ids": {
                "snapshot_id": str(uuid.uuid4()),
                "rebalancing_recommendation_id": str(uuid.uuid4()),
            },
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
        # flush must be awaitable: telemetry writes (record_ai_module_run) go
        # through this mock whenever a path reaches the formatter, and reach
        # `await db.flush()` once app.all_models has configured the mappers —
        # which any earlier-collected test importing it does. A plain MagicMock
        # made that order-dependent (TypeError on await).
        db=MagicMock(flush=AsyncMock()),
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

        # The first-turn chat reply path now routes through the shared
        # answer_formatter (build_fallback_brief is only the last-resort fallback).
        with (
            patch.object(
                mod, "compute_allocation_result", new=AsyncMock(return_value=outcome)
            ),
            patch.object(mod, "build_aa_facts_pack", return_value={}),
            patch.object(mod, "compute_current_asset_class_mix", return_value={}),
            patch(
                "app.domains.ai_engine.answer_formatter.formatter.format_answer",
                new=AsyncMock(return_value="formatted reply"),
            ),
            patch(
                "app.domains.ai_engine.answer_formatter.formatter.record_ai_module_run",
                new=AsyncMock(return_value=None),
            ),
        ):
            result = asyncio.run(mod.handle(_ctx("plan my retirement")))

        self.assertIsInstance(result, ChatHandlerResult)
        self.assertEqual(result.text, "formatted reply")
        self.assertEqual(result.snapshot_id, outcome.allocation_snapshot_id)
        self.assertEqual(
            result.rebalancing_recommendation_id, outcome.rebalancing_recommendation_id
        )

    def test_first_turn_blocking_message_passes_through(self):
        outcome = MagicMock()
        outcome.result = None
        outcome.blocking_message = "I need your date of birth..."
        outcome.allocation_snapshot_id = None
        outcome.rebalancing_recommendation_id = None

        with patch.object(
            mod, "compute_allocation_result", new=AsyncMock(return_value=outcome)
        ):
            result = asyncio.run(mod.handle(_ctx("plan my retirement")))

        self.assertEqual(result.text, "I need your date of birth...")
        self.assertIsNone(result.snapshot_id)


class NarrateModeTests(unittest.TestCase):
    def test_narrate_returns_text_no_engine(self):
        action = mod.ChatAction(mode="narrate")
        with (
            patch.object(mod, "_detect_action", new=AsyncMock(return_value=action)),
            patch.object(mod, "_rehydrate_last_alloc_output", return_value=MagicMock()),
            patch.object(mod, "build_aa_facts_pack", return_value={}),
            patch(
                "app.domains.ai_engine.answer_formatter.formatter.format_answer",
                new=AsyncMock(return_value="narration text"),
            ),
            patch(
                "app.domains.ai_engine.answer_formatter.formatter.record_ai_module_run",
                new=AsyncMock(return_value=None),
            ),
            patch.object(mod, "compute_allocation_result", new=AsyncMock()) as engine,
        ):
            result = asyncio.run(
                mod.handle(_ctx("is this too aggressive?", last_alloc=_agent_run()))
            )

        self.assertEqual(result.text, "narration text")
        engine.assert_not_called()


class EducateModeTests(unittest.TestCase):
    def test_educate_returns_text_no_engine(self):
        action = mod.ChatAction(mode="educate")
        with (
            patch.object(mod, "_detect_action", new=AsyncMock(return_value=action)),
            patch.object(mod, "_rehydrate_last_alloc_output", return_value=MagicMock()),
            patch.object(mod, "build_aa_facts_pack", return_value={}),
            patch(
                "app.domains.ai_engine.answer_formatter.formatter.format_answer",
                new=AsyncMock(return_value="educational text"),
            ),
            patch(
                "app.domains.ai_engine.answer_formatter.formatter.record_ai_module_run",
                new=AsyncMock(return_value=None),
            ),
            patch.object(mod, "compute_allocation_result", new=AsyncMock()) as engine,
        ):
            result = asyncio.run(
                mod.handle(_ctx("what does multi-cap mean?", last_alloc=_agent_run()))
            )

        self.assertEqual(result.text, "educational text")
        engine.assert_not_called()


class CounterfactualExploreTests(unittest.TestCase):
    def test_counterfactual_explore_runs_engine_no_persist(self):
        captured = {}

        async def fake_compute(user, question, **kwargs):
            captured["persist"] = kwargs.get("persist_recommendation")
            captured["db"] = kwargs.get("db")
            chat_ctx = kwargs.get("chat_ctx")
            captured["chat_ctx_overrides"] = (
                chat_ctx.chat_overrides if chat_ctx else None
            )
            return _engine_outcome_with_ids()

        action = mod.ChatAction(
            mode="counterfactual_explore", overrides={"effective_risk_score": 7.0}
        )
        with (
            patch.object(mod, "_detect_action", new=AsyncMock(return_value=action)),
            patch.object(mod, "compute_allocation_result", side_effect=fake_compute),
            patch.object(mod, "build_aa_facts_pack", return_value={}),
            patch.object(mod, "compute_current_asset_class_mix", return_value={}),
            patch(
                "app.domains.ai_engine.answer_formatter.formatter.format_answer",
                new=AsyncMock(return_value="hypothetical text"),
            ),
            patch(
                "app.domains.ai_engine.answer_formatter.formatter.record_ai_module_run",
                new=AsyncMock(return_value=None),
            ),
        ):
            result = asyncio.run(
                mod.handle(_ctx("what if risk were 7?", last_alloc=_agent_run()))
            )

        # The counterfactual path no longer appends a save offer.
        self.assertEqual(result.text, "hypothetical text")
        self.assertNotIn("save", result.text.lower())
        self.assertFalse(captured["persist"])
        self.assertIsNone(captured["db"])
        # Override flows via TurnContext.chat_overrides (NOT via setattr on User):
        self.assertEqual(captured["chat_ctx_overrides"], {"effective_risk_score": 7.0})
        self.assertIsNone(result.snapshot_id)

    def test_counterfactual_with_invalid_override_falls_to_redirect(self):
        action = mod.ChatAction(
            mode="counterfactual_explore", overrides={"unknown_key": 1.0}
        )
        with patch.object(mod, "_detect_action", new=AsyncMock(return_value=action)):
            result = asyncio.run(mod.handle(_ctx("what if?", last_alloc=_agent_run())))
        # Falls through to either redirect or invalid-override template; both mention Profile
        self.assertIn("Profile", result.text)


class ClarifyModeTests(unittest.TestCase):
    def test_clarify_returns_composed_question(self):
        action = mod.ChatAction(
            mode="clarify", clarification_question="What risk score?"
        )
        with patch.object(mod, "_detect_action", new=AsyncMock(return_value=action)):
            result = asyncio.run(
                mod.handle(_ctx("I want more risk", last_alloc=_agent_run()))
            )
        self.assertEqual(result.text, "What risk score?")
        self.assertIsNone(result.snapshot_id)

    def test_clarify_without_question_uses_fallback(self):
        action = mod.ChatAction(mode="clarify", clarification_question=None)
        with patch.object(mod, "_detect_action", new=AsyncMock(return_value=action)):
            result = asyncio.run(
                mod.handle(_ctx("I want something", last_alloc=_agent_run()))
            )
        self.assertTrue(result.text)


class RecomputeFullTests(unittest.TestCase):
    def test_recompute_full_runs_engine_and_persists(self):
        captured = {}

        async def fake_compute(user, question, **kwargs):
            captured["persist"] = kwargs.get("persist_recommendation")
            captured["db"] = kwargs.get("db")
            return _engine_outcome_with_ids()

        action = mod.ChatAction(mode="recompute_full")
        with (
            patch.object(mod, "_detect_action", new=AsyncMock(return_value=action)),
            patch.object(mod, "compute_allocation_result", side_effect=fake_compute),
            patch.object(mod, "build_aa_facts_pack", return_value={}),
            patch(
                "app.domains.ai_engine.answer_formatter.formatter.format_answer",
                new=AsyncMock(return_value="updated brief"),
            ),
            patch(
                "app.domains.ai_engine.answer_formatter.formatter.record_ai_module_run",
                new=AsyncMock(return_value=None),
            ),
        ):
            ctx = _ctx("redo my plan", last_alloc=_agent_run())
            result = asyncio.run(mod.handle(ctx))

        self.assertTrue(captured["persist"])
        self.assertIsNotNone(captured["db"])
        self.assertIsNotNone(result.snapshot_id)
        self.assertIsNotNone(result.rebalancing_recommendation_id)


class RedirectModeTests(unittest.TestCase):
    def test_redirect_routes_template_through_relay(self):
        action = mod.ChatAction(mode="redirect", redirect_reason="change holdings")
        relay = AsyncMock(return_value="RELAYED")
        with (
            patch.object(mod, "_detect_action", new=AsyncMock(return_value=action)),
            patch.object(mod, "format_relay_or_canned", new=relay),
        ):
            result = asyncio.run(
                mod.handle(_ctx("swap arbitrage", last_alloc=_agent_run()))
            )
        self.assertEqual(result.text, "RELAYED")
        kwargs = relay.call_args.kwargs
        self.assertEqual(kwargs["module_name"], "asset_allocation")
        # the resolved redirect template (with the reason) is what we relay
        self.assertIn("Profile", kwargs["message"])
        self.assertIn("change holdings", kwargs["message"])

    def test_invalid_override_routes_template_through_relay(self):
        action = mod.ChatAction(mode="counterfactual_explore", overrides={})
        relay = AsyncMock(return_value="RELAYED")
        with (
            patch.object(mod, "_detect_action", new=AsyncMock(return_value=action)),
            patch.object(mod, "format_relay_or_canned", new=relay),
        ):
            result = asyncio.run(
                mod.handle(_ctx("defer the rebalance", last_alloc=_agent_run()))
            )
        self.assertEqual(result.text, "RELAYED")
        kwargs = relay.call_args.kwargs
        self.assertEqual(kwargs["module_name"], "asset_allocation")
        self.assertEqual(kwargs["message"], mod._INVALID_OVERRIDE_TEMPLATE)


class DetectActionFailureTests(unittest.TestCase):
    def test_detect_action_failure_returns_degraded_text(self):
        with patch.object(
            mod, "_detect_action", new=AsyncMock(side_effect=RuntimeError("LLM down"))
        ):
            result = asyncio.run(mod.handle(_ctx("what?", last_alloc=_agent_run())))
        self.assertIn("rephrase", result.text)


class FallbackTests(unittest.TestCase):
    def test_first_turn_falls_back_to_brief_on_formatter_failure(self):
        outcome = _engine_outcome_with_ids()
        from app.domains.ai_engine.answer_formatter import FormatterFailure

        with (
            patch.object(
                mod, "compute_allocation_result", new=AsyncMock(return_value=outcome)
            ),
            patch.object(mod, "build_aa_facts_pack", return_value={}),
            patch(
                "app.domains.ai_engine.answer_formatter.formatter.format_answer",
                new=AsyncMock(side_effect=FormatterFailure("boom")),
            ),
            patch.object(
                mod, "build_fallback_brief", return_value="fallback brief text"
            ),
            patch(
                "app.domains.ai_engine.answer_formatter.formatter.record_ai_module_run",
                new=AsyncMock(return_value=None),
            ),
        ):
            result = asyncio.run(mod.handle(_ctx("plan my retirement")))

        self.assertEqual(result.text, "fallback brief text")


class RehydrateFallbackTests(unittest.TestCase):
    def test_rehydration_failure_returns_degraded_text(self):
        action = mod.ChatAction(mode="narrate")
        with (
            patch.object(mod, "_detect_action", new=AsyncMock(return_value=action)),
            patch.object(
                mod,
                "_rehydrate_last_alloc_output",
                side_effect=ValueError("schema drift"),
            ),
            patch(
                "app.domains.ai_engine.answer_formatter.formatter.format_answer",
                new=AsyncMock(),
            ) as fmt,
        ):
            result = asyncio.run(
                mod.handle(_ctx("explain my mix", last_alloc=_agent_run()))
            )
        self.assertIn("redo the plan", result.text)
        fmt.assert_not_called()


# The FormatterTelemetry tests that lived here previously asserted on columns
# the answer_formatter wrote. The first-turn / recompute / counterfactual paths
# now route back through the answer formatter (``_format_or_fallback``), same as
# narrate / educate — covered by their own tests below.


if __name__ == "__main__":
    unittest.main()
