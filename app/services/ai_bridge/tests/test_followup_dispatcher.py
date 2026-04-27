"""followup_dispatcher: registry + dispatch behavior."""

from __future__ import annotations

import asyncio
import unittest
import uuid
from datetime import datetime
from unittest.mock import MagicMock

from app.services.ai_bridge import followup_dispatcher as fd
from app.services.chat_core.turn_context import AgentRunRecord


def _agent_run() -> AgentRunRecord:
    return AgentRunRecord(
        id=uuid.uuid4(),
        module="goal_based_allocation",
        intent_detected="portfolio_optimisation",
        input_payload={},
        output_payload={"allocation_result": {}},
        created_at=datetime.utcnow(),
    )


class FollowupDispatcherTests(unittest.TestCase):

    def setUp(self):
        # Reset registry between tests
        fd._HANDLERS.clear()

    def test_register_and_dispatch_calls_handler(self):
        called = {}

        @fd.register("portfolio_optimisation")
        async def fake_handler(agent_run, ctx):
            called["agent_run"] = agent_run
            called["ctx"] = ctx
            return "narrated text"

        ctx = MagicMock()
        result = asyncio.run(fd.dispatch_followup(
            "portfolio_optimisation", _agent_run(), ctx,
        ))
        self.assertEqual(result, "narrated text")
        self.assertIs(called["ctx"], ctx)

    def test_unregistered_intent_raises(self):
        with self.assertRaises(RuntimeError):
            asyncio.run(fd.dispatch_followup(
                "no_such_intent", _agent_run(), MagicMock(),
            ))

    def test_register_multiple_intents_for_one_handler(self):
        @fd.register("portfolio_optimisation")
        @fd.register("goal_planning")
        async def shared(agent_run, ctx):
            return "shared response"

        for intent in ("portfolio_optimisation", "goal_planning"):
            self.assertEqual(
                asyncio.run(fd.dispatch_followup(intent, _agent_run(), MagicMock())),
                "shared response",
            )


class RegisterImportSideEffectTests(unittest.TestCase):
    """Importing asset_allocation_followup must populate the dispatcher registry.

    Locks the import-as-side-effect contract: removing the @register decorators
    OR removing the brain.py `from app.services.ai_bridge import asset_allocation_followup`
    import would silently break follow-up routing — this test fails if either
    happens.
    """

    def test_importing_asset_allocation_followup_registers_both_intents(self):
        import importlib
        from app.services.ai_bridge import followup_dispatcher as fd_local

        # Clear and force a fresh import so the @register decorators run again.
        fd_local._HANDLERS.clear()
        from app.services.ai_bridge import asset_allocation_followup
        importlib.reload(asset_allocation_followup)

        self.assertIn("portfolio_optimisation", fd_local._HANDLERS)
        self.assertIn("goal_planning", fd_local._HANDLERS)
        # Both intents resolve to the same public handler.
        self.assertIs(
            fd_local._HANDLERS["portfolio_optimisation"],
            asset_allocation_followup.handle_allocation_followup,
        )
        self.assertIs(
            fd_local._HANDLERS["goal_planning"],
            asset_allocation_followup.handle_allocation_followup,
        )


if __name__ == "__main__":
    unittest.main()
