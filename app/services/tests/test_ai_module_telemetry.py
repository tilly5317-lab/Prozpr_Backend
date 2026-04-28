"""Unit tests for record_ai_module_run payload kwargs."""

from __future__ import annotations

import asyncio
import unittest
import uuid
from unittest.mock import AsyncMock, MagicMock

from app.services.ai_module_telemetry import record_ai_module_run


class RecordAiModuleRunPayloadTests(unittest.TestCase):

    def test_payload_kwargs_persisted_on_row(self):
        """input_payload and output_payload are written when passed in."""
        added: list[object] = []
        db = MagicMock()
        db.add = MagicMock(side_effect=lambda row: added.append(row))
        db.flush = AsyncMock()

        asyncio.run(record_ai_module_run(
            db,
            user_id=uuid.uuid4(),
            session_id=uuid.uuid4(),
            module="goal_based_allocation",
            reason="full_pipeline_run",
            input_payload={"corpus": 8_000_000},
            output_payload={"allocation_result": {"grand_total": 8_000_000}},
            emit_standard_log=False,
        ))

        self.assertEqual(len(added), 1)
        row = added[0]
        self.assertEqual(row.input_payload, {"corpus": 8_000_000})
        self.assertEqual(row.output_payload, {"allocation_result": {"grand_total": 8_000_000}})

    def test_omitted_payload_kwargs_default_to_none(self):
        """Existing callers (no payload kwargs) keep persisting NULLs."""
        added: list[object] = []
        db = MagicMock()
        db.add = MagicMock(side_effect=lambda row: added.append(row))
        db.flush = AsyncMock()

        asyncio.run(record_ai_module_run(
            db,
            user_id=uuid.uuid4(),
            session_id=uuid.uuid4(),
            module="chat_flow",
            reason="some flow summary",
            emit_standard_log=False,
        ))

        self.assertEqual(len(added), 1)
        row = added[0]
        self.assertIsNone(row.input_payload)
        self.assertIsNone(row.output_payload)


if __name__ == "__main__":
    unittest.main()
