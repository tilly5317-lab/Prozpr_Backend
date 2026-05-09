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

        @cd.register("asset_allocation")
        async def fake_handler(ctx):
            called["ctx"] = ctx
            return ChatHandlerResult(text="hello")

        ctx = MagicMock()
        result = asyncio.run(cd.dispatch_chat("asset_allocation", ctx))
        self.assertIsInstance(result, ChatHandlerResult)
        self.assertEqual(result.text, "hello")
        self.assertIs(called["ctx"], ctx)

    def test_unregistered_intent_raises(self):
        with self.assertRaises(RuntimeError):
            asyncio.run(cd.dispatch_chat("no_such_intent", MagicMock()))

    def test_register_multiple_intents_for_one_handler(self):
        @cd.register("asset_allocation")
        @cd.register("goal_planning")
        async def shared(ctx):
            return ChatHandlerResult(text="shared")

        for intent in ("asset_allocation", "goal_planning"):
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


class RegisterImportSideEffectTests(unittest.TestCase):
    """Importing asset_allocation_chat must populate the dispatcher registry.

    Locks the import-as-side-effect contract: removing the @register decorator
    on handle() OR removing the brain.py
    `from app.services.ai_bridge.asset_allocation import chat as _aa_chat` import
    would silently break portfolio chat. Without this test, a future cleanup of
    the noqa: F401 import in brain.py would cause every portfolio turn to fall
    through to the safe-fallback canned message with no test signal.

    NOTE: as of the goal_planning routing fix, this handler is registered ONLY
    for asset_allocation. goal_planning is handled by a dedicated branch in
    brain.py that returns the classifier's canned message — it is intentionally
    NOT in the dispatcher registry. If a future change re-adds the
    @register("goal_planning") decorator to chat.py, the goal_planning branch
    in brain.py will be silently bypassed.
    """

    def test_importing_asset_allocation_chat_registers_only_asset_allocation(self):
        import importlib
        from app.services.ai_bridge import chat_dispatcher as cd

        # Clear and force a fresh import so the @register decorators run again.
        cd._HANDLERS.clear()
        from app.services.ai_bridge.asset_allocation import chat as asset_allocation_chat
        importlib.reload(asset_allocation_chat)

        # asset_allocation is registered.
        self.assertIn("asset_allocation", cd._HANDLERS)
        self.assertIs(
            cd._HANDLERS["asset_allocation"],
            asset_allocation_chat.handle,
        )
        # goal_planning is NOT registered — it is handled in brain.py via canned redirect.
        self.assertNotIn("goal_planning", cd._HANDLERS)


if __name__ == "__main__":
    unittest.main()
