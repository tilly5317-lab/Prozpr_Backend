"""Unit tests for speculative follow-up action-detection (audit F4). No network."""

from __future__ import annotations

import asyncio
import unittest
import uuid
from unittest.mock import MagicMock, patch

import app.all_models  # noqa: F401  (register ORM relationships)
from app.domains.ai_engine import chat_dispatcher
from app.domains.ai_engine.chat_dispatcher import (
    consume_speculative_detect,
    register_speculative_detector,
    speculative_detector_for,
)
from app.domains.ai_engine.services.brain import _start_speculative_detect
from app.domains.ai_engine.turn_context import TurnContext


def _ctx(**overrides) -> TurnContext:
    defaults = dict(
        user_ctx=MagicMock(),
        user_question="what if my risk were 7?",
        conversation_history=[],
        client_context=None,
        session_id=uuid.uuid4(),
        db=None,
        effective_user_id=uuid.uuid4(),
        last_agent_runs={},
        active_intent=None,
    )
    defaults.update(overrides)
    return TurnContext(**defaults)


class ConsumeSpeculativeDetectTests(unittest.TestCase):
    def test_no_task_returns_none(self):
        result = asyncio.run(consume_speculative_detect(_ctx()))
        self.assertIsNone(result)

    def test_completed_task_returns_value(self):
        async def run():
            async def detect():
                return "ACTION"

            task = asyncio.create_task(detect())
            await task
            return await consume_speculative_detect(_ctx(speculative_detect=task))

        self.assertEqual(asyncio.run(run()), "ACTION")

    def test_in_flight_task_is_awaited(self):
        async def run():
            async def detect():
                await asyncio.sleep(0.01)
                return "LATE_ACTION"

            task = asyncio.create_task(detect())
            return await consume_speculative_detect(_ctx(speculative_detect=task))

        self.assertEqual(asyncio.run(run()), "LATE_ACTION")

    def test_cancelled_task_returns_none(self):
        async def run():
            async def detect():
                await asyncio.sleep(10)

            task = asyncio.create_task(detect())
            task.cancel()
            await asyncio.sleep(0)  # let cancellation land
            return await consume_speculative_detect(_ctx(speculative_detect=task))

        self.assertIsNone(asyncio.run(run()))

    def test_failed_task_returns_none(self):
        async def run():
            async def detect():
                raise RuntimeError("llm exploded")

            task = asyncio.create_task(detect())
            return await consume_speculative_detect(_ctx(speculative_detect=task))

        self.assertIsNone(asyncio.run(run()))


class StartSpeculativeDetectTests(unittest.TestCase):
    """Brain-side gating. A stub detector is injected under a fake intent so no
    real module import or LLM call happens."""

    INTENT = "asset_allocation"

    def setUp(self):
        self._saved = chat_dispatcher._SPECULATIVE_DETECTORS.copy()
        # Point the brain's lazy-import map at an already-imported module so
        # importlib is a no-op, and register a stub detector.
        self._map_patch = patch.dict(
            "app.domains.ai_engine.services.brain._SPECULATIVE_DETECT_MODULES",
            {self.INTENT: "app.domains.ai_engine.chat_dispatcher"},
            clear=True,
        )
        self._map_patch.start()

        @register_speculative_detector(self.INTENT)
        async def stub_detector(ctx):
            return "STUB_ACTION"

    def tearDown(self):
        self._map_patch.stop()
        chat_dispatcher._SPECULATIVE_DETECTORS.clear()
        chat_dispatcher._SPECULATIVE_DETECTORS.update(self._saved)

    def test_starts_task_on_follow_up_with_active_intent(self):
        async def run():
            ctx = _ctx(
                active_intent=self.INTENT,
                last_agent_runs={self.INTENT: MagicMock()},
            )
            task = _start_speculative_detect(ctx)
            self.assertIsNotNone(task)
            return await task

        self.assertEqual(asyncio.run(run()), "STUB_ACTION")

    def test_no_task_without_prior_module_run(self):
        async def run():
            return _start_speculative_detect(
                _ctx(active_intent=self.INTENT, last_agent_runs={})
            )

        self.assertIsNone(asyncio.run(run()))

    def test_no_task_without_active_intent(self):
        async def run():
            return _start_speculative_detect(_ctx(active_intent=None))

        self.assertIsNone(asyncio.run(run()))

    def test_no_task_for_unmapped_intent(self):
        async def run():
            return _start_speculative_detect(
                _ctx(
                    active_intent="goal_planning",
                    last_agent_runs={"goal_planning": MagicMock()},
                )
            )

        self.assertIsNone(asyncio.run(run()))

    def test_registry_lookup(self):
        self.assertIsNotNone(speculative_detector_for(self.INTENT))
        self.assertIsNone(speculative_detector_for("no_such_intent"))


class RebalancingFirstTurnSpeculationTests(unittest.TestCase):
    """S2d: rebalancing speculates on a FIRST turn too (cold start), moving the
    detector off the critical path. Other mapped intents (asset_allocation)
    must keep bailing on a first turn — the relaxation is rebalancing-only."""

    def setUp(self):
        self._saved = chat_dispatcher._SPECULATIVE_DETECTORS.copy()
        self._map_patch = patch.dict(
            "app.domains.ai_engine.services.brain._SPECULATIVE_DETECT_MODULES",
            {
                "rebalancing": "app.domains.ai_engine.chat_dispatcher",
                "asset_allocation": "app.domains.ai_engine.chat_dispatcher",
            },
            clear=True,
        )
        self._map_patch.start()

        @register_speculative_detector("rebalancing")
        async def stub_rebal_detector(ctx):
            return "STUB_REBAL_ACTION"

    def tearDown(self):
        self._map_patch.stop()
        chat_dispatcher._SPECULATIVE_DETECTORS.clear()
        chat_dispatcher._SPECULATIVE_DETECTORS.update(self._saved)

    def test_starts_task_on_rebalancing_first_turn(self):
        async def run():
            ctx = _ctx(active_intent="rebalancing", last_agent_runs={})
            task = _start_speculative_detect(ctx)
            self.assertIsNotNone(task)
            return await task

        self.assertEqual(asyncio.run(run()), "STUB_REBAL_ACTION")

    def test_no_task_on_asset_allocation_first_turn(self):
        """Proves the relaxation is scoped to rebalancing, not every intent."""

        async def run():
            return _start_speculative_detect(
                _ctx(active_intent="asset_allocation", last_agent_runs={})
            )

        self.assertIsNone(asyncio.run(run()))


class HandlerConsumptionTests(unittest.TestCase):
    def test_aa_handler_uses_speculative_action_and_skips_serial_detect(self):
        """A clarify action from speculation is used directly — no LLM, no DB."""
        from app.domains.asset_allocation.services.aa_engine import chat as aa_chat

        async def run():
            action = aa_chat.ChatAction(
                mode="clarify", clarification_question="Would risk 7 work?"
            )

            async def detect():
                return action

            task = asyncio.create_task(detect())
            await task
            ctx = _ctx(
                last_agent_runs={"asset_allocation": MagicMock()},
                speculative_detect=task,
            )
            with patch.object(
                aa_chat,
                "_detect_action",
                side_effect=AssertionError("serial detect must not be called"),
            ):
                return await aa_chat.handle(ctx)

        result = asyncio.run(run())
        self.assertEqual(result.text, "Would risk 7 work?")


if __name__ == "__main__":
    unittest.main()
