"""Unit tests for load_conversation_history (LLM history chokepoint)."""

from __future__ import annotations

import asyncio
import unittest
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

# Importing all_models forces every domain's ORM classes to register with
# Base.metadata before ChatSession's relationship strings ("CashflowPlanRun",
# etc.) get resolved.
import app.all_models  # noqa: F401
from app.domains.chat.models.chat import ChatMessageRole
from app.domains.chat.services.chat_context import (
    _MAX_MESSAGE_CHARS,
    _TRUNCATION_MARKER,
    load_conversation_history,
)


TURN_AT = datetime(2026, 7, 25, 8, 27, 54, tzinfo=timezone.utc)


def _row(
    role: str,
    content: str,
    intent: str | None = None,
    created_at: datetime = TURN_AT,
) -> SimpleNamespace:
    """Stand-in for a ChatMessage ORM row.

    Uses the real ``ChatMessageRole`` — history is assembled into turns by
    matching on the enum members, so a stub role would never pair up.
    """
    return SimpleNamespace(
        role=ChatMessageRole(role),
        content=content,
        intent=intent,
        created_at=created_at,
    )


def _db_returning(rows: list[SimpleNamespace]) -> MagicMock:
    """Mock AsyncSession whose execute() yields the given rows (newest first)."""
    result = MagicMock()
    result.scalars.return_value.all.return_value = rows
    db = MagicMock()
    db.execute = AsyncMock(return_value=result)
    return db


class LoadConversationHistoryTests(unittest.TestCase):
    def test_rows_returned_chronologically(self):
        """Query returns newest-first; history must read oldest-first."""
        db = _db_returning(
            [_row("assistant", "second", intent="portfolio_query"), _row("user", "first")]
        )

        history = asyncio.run(load_conversation_history(uuid.uuid4(), db))

        self.assertEqual(
            history,
            [
                {
                    "role": "user",
                    "content": "first",
                    "intent": None,
                    "asked_at": TURN_AT,
                },
                {
                    "role": "assistant",
                    "content": "second",
                    "intent": "portfolio_query",
                    "asked_at": TURN_AT,
                },
            ],
        )

    def test_oversized_message_is_capped_with_marker(self):
        """A row that bypassed write-path caps is truncated before any LLM sees it."""
        giant = "x" * (_MAX_MESSAGE_CHARS + 5_000)
        # Whole turns only — a lone row would be dropped before any capping.
        db = _db_returning([_row("assistant", "reply"), _row("user", giant)])

        history = asyncio.run(load_conversation_history(uuid.uuid4(), db))

        content = history[0]["content"]
        self.assertEqual(len(content), _MAX_MESSAGE_CHARS + len(_TRUNCATION_MARKER))
        self.assertTrue(content.endswith(_TRUNCATION_MARKER))
        self.assertTrue(content.startswith("x"))

    def test_message_at_cap_is_untouched(self):
        """Content at exactly the cap passes through verbatim (no marker)."""
        at_cap = "y" * _MAX_MESSAGE_CHARS
        db = _db_returning([_row("assistant", at_cap), _row("user", "question")])

        history = asyncio.run(load_conversation_history(uuid.uuid4(), db))

        self.assertEqual(history[1]["content"], at_cap)


if __name__ == "__main__":
    unittest.main()
