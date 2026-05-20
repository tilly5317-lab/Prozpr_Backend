"""brain.run_turn: goal_planning branch dispatches through the new bridge.

Before the goal_planning bridge cutover this branch returned a canned
redirect; now it routes through ``dispatch_chat`` like asset_allocation
and rebalancing.
"""

from __future__ import annotations

import unittest
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.ai_bridge.chat_dispatcher import ChatHandlerResult
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

    async def test_goal_planning_dispatches_to_bridge(self):
        classification = MagicMock()
        classification.intent.value = "goal_planning"
        classification.confidence = 0.93
        classification.reasoning = "Customer asking feasibility question."
        classification.out_of_scope_message = None

        fake_turn_context = MagicMock()
        fake_turn_context.last_agent_runs = {}
        fake_turn_context.active_intent = None

        dispatch_result = ChatHandlerResult(text="bridge-formatted answer")

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
            new=AsyncMock(return_value=dispatch_result),
        ) as mock_dispatch:
            result = await ChatBrain().run_turn(_make_turn())

        self.assertEqual(result.content, "bridge-formatted answer")
        self.assertEqual(result.intent, "goal_planning")
        mock_dispatch.assert_awaited_once()
        # First positional arg to dispatch_chat is the intent string.
        args, _kwargs = mock_dispatch.await_args
        self.assertEqual(args[0], "goal_planning")


if __name__ == "__main__":
    unittest.main()
