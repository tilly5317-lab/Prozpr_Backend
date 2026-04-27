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


if __name__ == "__main__":
    unittest.main()
