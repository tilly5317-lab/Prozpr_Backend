"""chat_dispatcher: registry + dispatch behavior (new signature)."""

from __future__ import annotations

import asyncio
import unittest
import uuid
from unittest.mock import MagicMock

from app.services.ai_bridge import chat_dispatcher as cd
from app.services.ai_bridge.chat_dispatcher import ChatHandlerResult


class ChatDispatcherTests(unittest.TestCase):

    def setUp(self):
        cd._HANDLERS.clear()

    def test_register_and_dispatch_calls_handler(self):
        called = {}

        @cd.register("portfolio_optimisation")
        async def fake_handler(ctx):
            called["ctx"] = ctx
            return ChatHandlerResult(text="hello")

        ctx = MagicMock()
        result = asyncio.run(cd.dispatch_chat("portfolio_optimisation", ctx))
        self.assertIsInstance(result, ChatHandlerResult)
        self.assertEqual(result.text, "hello")
        self.assertIs(called["ctx"], ctx)

    def test_unregistered_intent_raises(self):
        with self.assertRaises(RuntimeError):
            asyncio.run(cd.dispatch_chat("no_such_intent", MagicMock()))

    def test_register_multiple_intents_for_one_handler(self):
        @cd.register("portfolio_optimisation")
        @cd.register("goal_planning")
        async def shared(ctx):
            return ChatHandlerResult(text="shared")

        for intent in ("portfolio_optimisation", "goal_planning"):
            self.assertEqual(
                asyncio.run(cd.dispatch_chat(intent, MagicMock())).text,
                "shared",
            )

    def test_chat_handler_result_carries_optional_ids(self):
        snap = uuid.uuid4()
        rec = uuid.uuid4()
        result = ChatHandlerResult(text="ok", snapshot_id=snap, rebalancing_recommendation_id=rec)
        self.assertEqual(result.snapshot_id, snap)
        self.assertEqual(result.rebalancing_recommendation_id, rec)
        self.assertIsNone(ChatHandlerResult(text="x").snapshot_id)
        self.assertIsNone(ChatHandlerResult(text="x").rebalancing_recommendation_id)


if __name__ == "__main__":
    unittest.main()
