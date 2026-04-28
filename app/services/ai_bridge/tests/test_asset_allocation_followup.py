"""asset_allocation_followup: handler narrates persisted snapshots."""

from __future__ import annotations

import asyncio
import unittest
import uuid
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.ai_bridge import asset_allocation_followup as mod
from app.services.chat_core.turn_context import AgentRunRecord, TurnContext


def _agent_run() -> AgentRunRecord:
    return AgentRunRecord(
        id=uuid.uuid4(),
        module="goal_based_allocation",
        intent_detected="portfolio_optimisation",
        input_payload={"effective_risk_score": 5.4, "age": 39, "total_corpus": 8_000_000},
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
        },
        created_at=datetime.utcnow(),
    )


def _ctx(question: str) -> TurnContext:
    return TurnContext(
        user_ctx=MagicMock(date_of_birth=None, first_name="Tilly"),
        user_question=question,
        conversation_history=[],
        client_context=None,
        session_id=uuid.uuid4(),
        db=None,
        effective_user_id=uuid.uuid4(),
        last_agent_runs={},
        active_intent="portfolio_optimisation",
    )


class NarratePathTests(unittest.TestCase):

    def test_narrate_path_returns_llm_text(self):
        action = mod.FollowupAction(mode="narrate")
        with patch.object(mod, "_detect_action",
                          new=AsyncMock(return_value=action)), \
             patch.object(mod, "_narrate_with_llm",
                          new=AsyncMock(return_value="narrated answer")):
            text = asyncio.run(mod.handle_allocation_followup(
                _agent_run(), _ctx("is this too aggressive?"),
            ))
        self.assertEqual(text, "narrated answer")

    def test_redirect_mutation_returns_template(self):
        action = mod.FollowupAction(
            mode="redirect_mutation",
            redirect_reason="change your holdings",
        )
        with patch.object(mod, "_detect_action",
                          new=AsyncMock(return_value=action)):
            text = asyncio.run(mod.handle_allocation_followup(
                _agent_run(), _ctx("swap arbitrage for liquid"),
            ))
        # Template references Profile section
        self.assertIn("Profile", text)
        self.assertIn("change your holdings", text)


class CounterfactualPathTests(unittest.TestCase):

    def test_counterfactual_runs_engine_with_override_no_persistence(self):
        from app.services.ai_bridge import asset_allocation_followup_counterfactual as cf

        captured = {}

        async def fake_compute(user, question, *, db, persist_recommendation,
                                acting_user_id, chat_session_id, spine_mode):
            captured["persist"] = persist_recommendation
            captured["db"] = db
            captured["spine_mode"] = spine_mode
            captured["override_seen"] = getattr(user, "_chat_risk_score_override", None)
            outcome = MagicMock()
            outcome.result = MagicMock()
            outcome.result.grand_total = 8_000_000
            outcome.result.model_dump = MagicMock(return_value={"grand_total": 8_000_000})
            outcome.blocking_message = None
            return outcome

        agent_run = AgentRunRecord(
            id=uuid.uuid4(),
            module="goal_based_allocation",
            intent_detected="portfolio_optimisation",
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
            },
            created_at=datetime.utcnow(),
        )

        with patch.object(cf, "compute_allocation_result", new=fake_compute), \
             patch.object(cf, "_narrate_counterfactual",
                          new=AsyncMock(return_value="hypothetical text")):
            text = asyncio.run(cf.run_counterfactual(
                agent_run, _ctx("what if my risk were 7?"),
                {"effective_risk_score": 7.0},
            ))

        self.assertEqual(text, "hypothetical text")
        self.assertFalse(captured["persist"])
        self.assertIsNone(captured["db"])
        self.assertEqual(captured["spine_mode"], "counterfactual")
        self.assertEqual(captured["override_seen"], 7.0)

    def test_invalid_override_falls_through_to_redirect(self):
        from app.services.ai_bridge import asset_allocation_followup_counterfactual as cf

        agent_run = AgentRunRecord(
            id=uuid.uuid4(),
            module="goal_based_allocation",
            intent_detected="portfolio_optimisation",
            input_payload={"effective_risk_score": 5.4},
            output_payload={"allocation_result": {}},
            created_at=datetime.utcnow(),
        )
        text = asyncio.run(cf.run_counterfactual(
            agent_run, _ctx("what if my goal amount were higher?"),
            {"goal_amount": 50_000_000},
        ))
        self.assertIn("Profile", text)

    def test_empty_overrides_falls_through_to_redirect(self):
        from app.services.ai_bridge import asset_allocation_followup_counterfactual as cf

        agent_run = AgentRunRecord(
            id=uuid.uuid4(),
            module="goal_based_allocation",
            intent_detected="portfolio_optimisation",
            input_payload={"effective_risk_score": 5.4},
            output_payload={"allocation_result": {}},
            created_at=datetime.utcnow(),
        )
        text = asyncio.run(cf.run_counterfactual(
            agent_run, _ctx("what if?"), {},
        ))
        self.assertIn("Profile", text)


class ClarifyPathTests(unittest.TestCase):

    def test_clarify_returns_composed_question(self):
        action = mod.FollowupAction(
            mode="clarify",
            clarification_question="What risk score would you like to try?",
        )
        with patch.object(mod, "_detect_action",
                          new=AsyncMock(return_value=action)):
            text = asyncio.run(mod.handle_allocation_followup(
                _agent_run(), _ctx("I can take more risk."),
            ))
        self.assertEqual(text, "What risk score would you like to try?")

    def test_clarify_without_question_uses_fallback(self):
        action = mod.FollowupAction(mode="clarify", clarification_question=None)
        with patch.object(mod, "_detect_action",
                          new=AsyncMock(return_value=action)):
            text = asyncio.run(mod.handle_allocation_followup(
                _agent_run(), _ctx("I want something different"),
            ))
        # The fallback is returned (not empty, mentions risk score / amount)
        self.assertTrue(text)
        self.assertIn("risk score", text.lower())


if __name__ == "__main__":
    unittest.main()
