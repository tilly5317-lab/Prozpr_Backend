"""brain.run_turn: goal_planning branch returns the canned redirect."""

from __future__ import annotations

import unittest
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.chat_core.brain import ChatBrain
from app.services.chat_core.types import ChatTurnInput


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


class BrainGoalPlanningBranchTests(unittest.IsolatedAsyncioTestCase):

    async def test_goal_planning_returns_canned_message_and_does_not_dispatch(self):
        canned = "Goal planning is coming — ask me about allocation in the meantime."

        # Mock classification result
        classification = MagicMock()
        classification.intent.value = "goal_planning"
        classification.confidence = 0.93
        classification.reasoning = "Customer asking feasibility question."
        classification.out_of_scope_message = canned

        # Mock turn context
        fake_turn_context = MagicMock()
        fake_turn_context.last_agent_runs = {}
        fake_turn_context.active_intent = None

        with patch(
            "app.services.chat_core.brain.build_turn_context",
            new=AsyncMock(return_value=fake_turn_context),
        ), patch(
            "app.services.chat_core.brain.classify_user_message",
            new=AsyncMock(return_value=classification),
        ), patch(
            "app.services.chat_core.brain.log_chat_turn_flow_summary",
            new=AsyncMock(return_value=None),
        ), patch(
            "app.services.ai_bridge.chat_dispatcher.dispatch_chat",
            new=AsyncMock(),
        ) as mock_dispatch:
            result = await ChatBrain().run_turn(_make_turn())

        self.assertEqual(result.content, canned)
        self.assertEqual(result.intent, "goal_planning")
        mock_dispatch.assert_not_called()

    async def test_goal_planning_falls_back_when_canned_message_missing(self):
        # Defensive: classifier returns goal_planning without populating
        # out_of_scope_message. The brain branch must still produce a string.
        classification = MagicMock()
        classification.intent.value = "goal_planning"
        classification.confidence = 0.5
        classification.reasoning = "low-confidence goal classification."
        classification.out_of_scope_message = None

        fake_turn_context = MagicMock()
        fake_turn_context.last_agent_runs = {}
        fake_turn_context.active_intent = None

        with patch(
            "app.services.chat_core.brain.build_turn_context",
            new=AsyncMock(return_value=fake_turn_context),
        ), patch(
            "app.services.chat_core.brain.classify_user_message",
            new=AsyncMock(return_value=classification),
        ), patch(
            "app.services.chat_core.brain.log_chat_turn_flow_summary",
            new=AsyncMock(return_value=None),
        ):
            result = await ChatBrain().run_turn(_make_turn())

        self.assertIsInstance(result.content, str)
        self.assertGreater(len(result.content), 0)
        self.assertEqual(result.intent, "goal_planning")


if __name__ == "__main__":
    unittest.main()
