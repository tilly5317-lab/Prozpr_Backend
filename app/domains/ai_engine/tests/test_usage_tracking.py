"""Unit tests for per-turn LLM usage tracking (audit F9). No network calls."""

from __future__ import annotations

import asyncio
import unittest
import uuid
from unittest.mock import AsyncMock, MagicMock

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage

import app.all_models  # noqa: F401  (register ORM relationships)
from app.domains.ai_engine.usage_tracking import (
    jsonable_llm_usage,
    track_formatter_llm_usage,
    track_turn_llm_usage,
    usage_totals,
)
from app.domains.chat.services.ai_module_telemetry import log_chat_turn_flow_summary


def _fake_llm() -> FakeMessagesListChatModel:
    """One-response fake chat model whose reply carries usage metadata.

    The usage handler keys on BOTH ``usage_metadata`` AND
    ``response_metadata["model_name"]`` — real providers set both; the fake
    message must too or the call is silently dropped.
    """
    msg = AIMessage(
        content="ok",
        usage_metadata={"input_tokens": 100, "output_tokens": 20, "total_tokens": 120},
        response_metadata={"model_name": "fake-haiku"},
    )
    return FakeMessagesListChatModel(responses=[msg])


class TrackerTests(unittest.TestCase):
    def test_captures_call_inside_context(self):
        with track_turn_llm_usage() as cb:
            _fake_llm().invoke("hi")
        totals = usage_totals(cb.usage_metadata)
        self.assertEqual(totals, (100, 20))

    def test_nested_trackers_both_capture(self):
        """Formatter tracker nests inside the turn tracker; both see the call."""
        with track_turn_llm_usage() as turn_cb:
            with track_formatter_llm_usage() as fmt_cb:
                _fake_llm().invoke("hi")
        self.assertEqual(usage_totals(turn_cb.usage_metadata), (100, 20))
        self.assertEqual(usage_totals(fmt_cb.usage_metadata), (100, 20))

    def test_no_capture_outside_context(self):
        with track_turn_llm_usage() as cb:
            pass
        _fake_llm().invoke("hi")  # after exit — must not be attributed
        self.assertEqual(cb.usage_metadata, {})

    def test_repeated_use_does_not_grow_configure_hooks(self):
        """Leak regression: our module-level vars are registered once, ever.

        (langchain's own get_usage_metadata_callback appends a global hook per
        use — the reason usage_tracking exists.)
        """
        from langchain_core.tracers.context import _configure_hooks

        before = len(_configure_hooks)
        for _ in range(5):
            with track_turn_llm_usage():
                pass
            with track_formatter_llm_usage():
                pass
        self.assertEqual(len(_configure_hooks), before)


class HelperTests(unittest.TestCase):
    def test_jsonable_empty_is_none(self):
        self.assertIsNone(jsonable_llm_usage(None))
        self.assertIsNone(jsonable_llm_usage({}))

    def test_totals_sum_across_models(self):
        usage = {
            "claude-haiku-4-5-20251001": {"input_tokens": 7000, "output_tokens": 120},
            "claude-sonnet-4-6": {"input_tokens": 1000, "output_tokens": 300},
        }
        self.assertEqual(usage_totals(usage), (8000, 420))


class TelemetryForwardingTests(unittest.TestCase):
    def test_flow_summary_persists_llm_usage_in_extra(self):
        added: list[object] = []
        db = MagicMock()
        db.add = MagicMock(side_effect=lambda row: added.append(row))
        db.flush = AsyncMock()

        usage = {"claude-haiku-4-5-20251001": {"input_tokens": 10, "output_tokens": 2}}
        asyncio.run(
            log_chat_turn_flow_summary(
                db,
                user_id=uuid.uuid4(),
                session_id=uuid.uuid4(),
                intent="portfolio_query",
                steps=["identified intent: portfolio_query"],
                duration_ms=1234,
                llm_usage=usage,
            )
        )

        self.assertEqual(len(added), 1)
        self.assertEqual(added[0].extra, {"llm_usage": usage})

    def test_flow_summary_without_usage_keeps_extra_null(self):
        added: list[object] = []
        db = MagicMock()
        db.add = MagicMock(side_effect=lambda row: added.append(row))
        db.flush = AsyncMock()

        asyncio.run(
            log_chat_turn_flow_summary(
                db,
                user_id=uuid.uuid4(),
                session_id=uuid.uuid4(),
                intent="general_chat",
                steps=["identified intent: general_chat"],
            )
        )

        self.assertEqual(len(added), 1)
        self.assertIsNone(added[0].extra)


if __name__ == "__main__":
    unittest.main()
