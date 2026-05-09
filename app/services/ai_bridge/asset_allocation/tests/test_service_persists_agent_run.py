"""Verify compute_allocation_result writes a structured AgentRun row."""

from __future__ import annotations

import asyncio
import unittest
import uuid
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.ai_bridge.asset_allocation import service as svc


class _FakeUser:
    def __init__(self):
        self.date_of_birth = date(1986, 1, 1)


class AllocationPersistsAgentRunTests(unittest.TestCase):

    def test_agent_run_row_written_with_payloads(self):
        captured: dict = {}

        async def fake_record(db, **kwargs):
            captured.update(kwargs)
            return uuid.uuid4()

        # Build a minimal AllocationInput stub
        alloc_input = MagicMock()
        alloc_input.model_dump = MagicMock(return_value={"corpus": 8_000_000})
        alloc_input.effective_risk_score = 5.4
        alloc_input.risk_willingness = None
        alloc_input.risk_capacity_score = None
        alloc_input.age = 39
        alloc_input.total_corpus = 8_000_000
        alloc_input.goals = []

        # Build a minimal output stub
        output = MagicMock()
        output.grand_total = 8_000_000
        output.model_dump = MagicMock(return_value={"grand_total": 8_000_000})

        with patch.object(svc, "build_goal_allocation_input_for_user",
                          return_value=(alloc_input, {})), \
             patch.object(svc.asyncio, "to_thread",
                          new=AsyncMock(return_value=({"step7_output": {}}, output))), \
             patch.object(svc, "record_ai_module_run", side_effect=fake_record), \
             patch("app.services.ai_bridge.asset_allocation.service.get_settings") as gs:
            gs.return_value.get_anthropic_asset_allocation_key.return_value = "sk-fake"

            db = MagicMock()
            asyncio.run(svc.compute_allocation_result(
                _FakeUser(), "test question",
                db=db, persist_recommendation=False,
                acting_user_id=uuid.uuid4(), chat_session_id=uuid.uuid4(),
            ))

        self.assertEqual(captured.get("module"), "asset_allocation")
        self.assertIn("input_payload", captured)
        self.assertIn("output_payload", captured)
        self.assertEqual(captured["input_payload"], {"corpus": 8_000_000})
        self.assertIn("allocation_result", captured["output_payload"])
        self.assertIn("correlation_ids", captured["output_payload"])

    def test_agent_run_persistence_failure_does_not_break_allocation(self):
        """If record_ai_module_run raises, the user still gets their allocation."""
        async def boom(db, **kwargs):
            raise RuntimeError("simulated DB failure")

        alloc_input = MagicMock()
        alloc_input.model_dump = MagicMock(return_value={"corpus": 8_000_000})

        output = MagicMock()
        output.grand_total = 8_000_000
        output.model_dump = MagicMock(return_value={"grand_total": 8_000_000})

        with patch.object(svc, "build_goal_allocation_input_for_user",
                          return_value=(alloc_input, {})), \
             patch.object(svc.asyncio, "to_thread",
                          new=AsyncMock(return_value=({"step7_output": {}}, output))), \
             patch.object(svc, "record_ai_module_run", side_effect=boom), \
             patch("app.services.ai_bridge.asset_allocation.service.get_settings") as gs:
            gs.return_value.get_anthropic_asset_allocation_key.return_value = "sk-fake"

            db = MagicMock()
            outcome = asyncio.run(svc.compute_allocation_result(
                _FakeUser(), "test question",
                db=db, persist_recommendation=False,
                acting_user_id=uuid.uuid4(), chat_session_id=uuid.uuid4(),
            ))

        # The non-fatal guarantee: outcome carries the allocation result, no exception
        self.assertIs(outcome.result, output)
        self.assertIsNone(outcome.blocking_message)


if __name__ == "__main__":
    unittest.main()
