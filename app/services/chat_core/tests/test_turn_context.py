"""TurnContext builder tests."""

from __future__ import annotations

import asyncio
import unittest
import uuid
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

from app.services.chat_core.turn_context import (
    AgentRunRecord, TurnContext, build_turn_context,
)


class _StubResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows

    def scalars(self):
        # Mimic SQLAlchemy's ScalarResult: .all() returns the row objects.
        class _ScalarResult:
            def __init__(self, rows):
                self._rows = rows
            def all(self):
                return self._rows
        return _ScalarResult(self._rows)

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None


class TurnContextBuilderTests(unittest.TestCase):

    def test_loads_last_agent_run_per_module_and_active_intent(self):
        sid = uuid.uuid4()

        alloc_row = MagicMock(
            id=uuid.uuid4(),
            module="asset_allocation",
            intent_detected="asset_allocation",
            input_payload={"corpus": 8_000_000},
            output_payload={"allocation_result": {"grand_total": 8_000_000}},
            created_at=datetime(2026, 4, 27, 9, 0),
        )
        db = MagicMock()
        db.execute = AsyncMock(side_effect=[
            _StubResult([alloc_row]),
            _StubResult(["asset_allocation"]),           # last intent_detected (scalar)
        ])

        turn = MagicMock(
            user_ctx=MagicMock(),
            user_question="is this too aggressive?",
            conversation_history=[],
            client_context=None,
            session_id=sid,
            db=db,
            user_id=uuid.uuid4(),
            effective_user_id=uuid.uuid4(),
        )

        ctx = asyncio.run(build_turn_context(turn))

        self.assertIn("asset_allocation", ctx.last_agent_runs)
        rec: AgentRunRecord = ctx.last_agent_runs["asset_allocation"]
        self.assertEqual(rec.module, "asset_allocation")
        self.assertEqual(rec.input_payload, {"corpus": 8_000_000})
        self.assertEqual(ctx.active_intent, "asset_allocation")

    def test_empty_session_returns_empty_runs(self):
        sid = uuid.uuid4()
        db = MagicMock()
        db.execute = AsyncMock(side_effect=[
            _StubResult([]),
            _StubResult([]),
        ])
        turn = MagicMock(
            user_ctx=MagicMock(),
            user_question="hello",
            conversation_history=[],
            client_context=None,
            session_id=sid,
            db=db,
            user_id=uuid.uuid4(),
            effective_user_id=uuid.uuid4(),
        )

        ctx = asyncio.run(build_turn_context(turn))

        self.assertEqual(ctx.last_agent_runs, {})
        self.assertIsNone(ctx.active_intent)

    def test_no_db_returns_empty_context(self):
        """If db is None (no chat session), context degrades gracefully."""
        turn = MagicMock(
            user_ctx=MagicMock(),
            user_question="hello",
            conversation_history=[],
            client_context=None,
            session_id=uuid.uuid4(),
            db=None,
            user_id=uuid.uuid4(),
            effective_user_id=uuid.uuid4(),
        )
        ctx = asyncio.run(build_turn_context(turn))
        self.assertEqual(ctx.last_agent_runs, {})
        self.assertIsNone(ctx.active_intent)

    def test_query_failure_degrades_to_empty(self):
        """If a DB query raises, context falls back to empty rather than crashing."""
        db = MagicMock()
        db.execute = AsyncMock(side_effect=RuntimeError("simulated DB outage"))
        turn = MagicMock(
            user_ctx=MagicMock(),
            user_question="hello",
            conversation_history=[],
            client_context=None,
            session_id=uuid.uuid4(),
            db=db,
            user_id=uuid.uuid4(),
            effective_user_id=uuid.uuid4(),
        )
        ctx = asyncio.run(build_turn_context(turn))
        self.assertEqual(ctx.last_agent_runs, {})
        self.assertIsNone(ctx.active_intent)


class TurnContextChatOverridesFieldTests(unittest.TestCase):
    """PR 1: chat_overrides field on the frozen dataclass."""

    def test_turn_context_accepts_chat_overrides_kwarg(self):
        ctx = TurnContext(
            user_ctx=MagicMock(),
            user_question="x",
            conversation_history=[],
            client_context=None,
            session_id=uuid.uuid4(),
            db=None,
            effective_user_id=uuid.uuid4(),
            last_agent_runs={},
            active_intent=None,
            chat_overrides={"effective_risk_score": 7},
        )
        self.assertEqual(ctx.chat_overrides, {"effective_risk_score": 7})


if __name__ == "__main__":
    unittest.main()
