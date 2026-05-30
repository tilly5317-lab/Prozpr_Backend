"""brain.run_turn: goal_planning intent runs the cashflow AI module sequence.

After the SEQUENCES refactor: the goal_planning intent maps to
``[AIModule.CASHFLOW]``. The cashflow module service delegates to
``dispatch_chat("goal_planning", ctx)`` and returns a ``ModuleOutput`` whose
``text`` becomes the final assistant reply.
"""

from __future__ import annotations

import unittest
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

from app.domains.ai_engine.services.chat_dispatcher import ChatHandlerResult
from app.domains.ai_engine.services.chat_orchestrator.brain import ChatBrain
from app.domains.ai_engine.services.chat_orchestrator.types import ChatTurnInput


def _make_turn() -> ChatTurnInput:
    return ChatTurnInput(
        user_id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        db=None,
        user_question="I want to retire in 15 years with 5 crore — is that possible?",
        conversation_history=[],
        user_ctx=MagicMock(),
        client_context=None,
    )


class BrainGoalPlanningSequenceTests(unittest.IsolatedAsyncioTestCase):

    async def test_goal_planning_runs_cashflow_module(self):
        # Fake classifier output — the intent classifier service is the FIRST
        # module the brain runs; we mock its underlying agent call.
        classification = MagicMock()
        classification.intent.value = "goal_planning"
        classification.confidence = 0.93
        classification.reasoning = "Customer asking feasibility question."
        classification.out_of_scope_message = None

        fake_turn_context = MagicMock()
        fake_turn_context.last_agent_runs = {}
        fake_turn_context.active_intent = None
        fake_turn_context.awaiting_save = False

        dispatch_result = ChatHandlerResult(text="cashflow module formatted answer")

        with patch(
            "app.domains.ai_engine.services.chat_orchestrator.brain.build_turn_context",
            new=AsyncMock(return_value=fake_turn_context),
        ), patch(
            # The intent_classifier service re-exports the classifier from its
            # legacy home; patch the legacy symbol so both call sites observe it.
            "app.domains.ai_engine.services.bridges.intent_classifier_service.classify_user_message",
            new=AsyncMock(return_value=classification),
        ), patch(
            "app.domains.ai_engine.services.chat_orchestrator.brain.log_chat_turn_flow_summary",
            new=AsyncMock(return_value=None),
        ), patch(
            "app.domains.ai_engine.services.chat_dispatcher.dispatch_chat",
            new=AsyncMock(return_value=dispatch_result),
        ) as mock_dispatch:
            result = await ChatBrain().run_turn(_make_turn())

        self.assertEqual(result.content, "cashflow module formatted answer")
        self.assertEqual(result.intent, "goal_planning")
        # The cashflow module is the only downstream module in the goal_planning
        # sequence, and it delegates to dispatch_chat("goal_planning", ctx).
        mock_dispatch.assert_awaited_once()
        args, _kwargs = mock_dispatch.await_args
        self.assertEqual(args[0], "goal_planning")


if __name__ == "__main__":
    unittest.main()
