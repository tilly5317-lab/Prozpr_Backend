"""Tests for the shared answer_formatter — prompt assembly + types + fallback."""

from __future__ import annotations

import asyncio
import unittest
import uuid

import pytest
from unittest.mock import AsyncMock, patch

from app.domains.ai_engine.answer_formatter import (
    FORMATTER_HOUSE_STYLE,
    FormatterFailure,
    assemble_prompt,
    format_answer,
)


def test_assemble_prompt_includes_house_style_and_body():
    prompt = assemble_prompt(
        question="why so much in debt?",
        action_mode="narrate",
        module_name="asset_allocation",
        facts_pack={"risk_score": 5.5, "asset_class_mix_pct": {"equity": 40.0, "debt": 51.0, "others": 9.0}},
        body_prompt="MODULE-BODY",
        history=[{"role": "user", "content": "what's my mix?"}],
        profile={"age": 39, "total_corpus_inr": 8_000_000},
    )
    assert FORMATTER_HOUSE_STYLE in prompt["system"]
    assert "MODULE-BODY" in prompt["system"]
    assert "why so much in debt?" in prompt["user"]
    assert "narrate" in prompt["user"]
    assert "5.5" in prompt["user"]
    assert "40.0" in prompt["user"]


def test_assemble_prompt_truncates_long_history():
    long_history = [{"role": "user", "content": f"msg {i}"} for i in range(50)]
    prompt = assemble_prompt(
        question="?", action_mode="narrate", module_name="x",
        facts_pack={}, body_prompt="b", history=long_history, profile={},
    )
    # Only the last 6 history entries should appear.
    assert "msg 49" in prompt["user"]
    assert "msg 0" not in prompt["user"]


def test_house_style_contains_required_prohibitions():
    """Guard rail: prohibitions must be present so future edits don't drop them."""
    text = FORMATTER_HOUSE_STYLE.lower()
    # Funds-and-ISIN prohibition: don't invent funds; never quote ISINs.
    assert "don't invent or recommend mutual funds" in text or "no specific fund" in text
    assert "never quote isins" in text or "never recommend" in text and "isin" in text
    # Numbers prohibition.
    assert "never invent numbers" in text or "do not invent numbers" in text


def test_formatter_failure_is_an_exception():
    err = FormatterFailure("boom")
    assert isinstance(err, Exception)
    assert "boom" in str(err)


# ---------------------------------------------------------------------------
# LLM call tests (Task 3)
# ---------------------------------------------------------------------------


def test_format_answer_returns_text_on_success():
    with patch(
        "app.domains.ai_engine.answer_formatter.formatter._invoke_llm",
        new=AsyncMock(return_value="Here's your tailored answer."),
    ):
        out = asyncio.run(format_answer(
            question="?", action_mode="narrate", module_name="x",
            facts_pack={"k": 1}, body_prompt="b", history=[], profile={},
        ))
    assert out == "Here's your tailored answer."


def test_format_answer_raises_formatter_failure_on_empty_response():
    with patch(
        "app.domains.ai_engine.answer_formatter.formatter._invoke_llm",
        new=AsyncMock(return_value=""),
    ):
        with pytest.raises(FormatterFailure):
            asyncio.run(format_answer(
                question="?", action_mode="narrate", module_name="x",
                facts_pack={}, body_prompt="b", history=[], profile={},
            ))


def test_format_answer_raises_formatter_failure_on_llm_exception():
    with patch(
        "app.domains.ai_engine.answer_formatter.formatter._invoke_llm",
        new=AsyncMock(side_effect=RuntimeError("api down")),
    ):
        with pytest.raises(FormatterFailure):
            asyncio.run(format_answer(
                question="?", action_mode="narrate", module_name="x",
                facts_pack={}, body_prompt="b", history=[], profile={},
            ))


def test_format_answer_propagates_truncation_failure_unwrapped():
    """FormatterFailure from _invoke_llm (e.g. max_tokens truncation) must pass
    through verbatim — not get re-wrapped as `formatter_llm_call_failed`."""
    with patch(
        "app.domains.ai_engine.answer_formatter.formatter._invoke_llm",
        new=AsyncMock(side_effect=FormatterFailure("formatter_truncated_at_max_tokens")),
    ):
        with pytest.raises(FormatterFailure, match="formatter_truncated_at_max_tokens"):
            asyncio.run(format_answer(
                question="?", action_mode="narrate", module_name="x",
                facts_pack={}, body_prompt="b", history=[], profile={},
            ))


def test_invoke_llm_raises_formatter_failure_when_response_truncated():
    """Hitting max_tokens must still raise FormatterFailure so the bridge falls back
    to the deterministic brief instead of returning a half-rendered answer."""
    from app.domains.ai_engine.answer_formatter import formatter as fmt

    class _FakeMessage:
        tool_calls = []                                   # truncated mid tool-call
        response_metadata = {"stop_reason": "max_tokens"}

    class _BoundLLM:
        async def ainvoke(self, _msgs):
            return _FakeMessage()

    class _FakeLLM:
        def __init__(self, **_kw):
            pass

        def bind_tools(self, *_a, **_kw):
            return _BoundLLM()

    with patch("langchain_anthropic.ChatAnthropic", _FakeLLM), \
         patch("app.core.config.get_settings") as gs:
        gs.return_value.get_anthropic_answer_formatter_key.return_value = "sk-test"
        with pytest.raises(FormatterFailure, match="truncated"):
            asyncio.run(fmt._invoke_llm("sys", "user", "goal_planning"))


def test_invoke_llm_returns_answer_field():
    """The answer-only forced tool returns the `answer` field as the reply."""
    from app.domains.ai_engine.answer_formatter import formatter as fmt

    class _FakeMessage:
        tool_calls = [{
            "name": "return_formatted_answer",
            "args": {"answer": "You're an **Aggressive** investor."},
        }]
        response_metadata = {"stop_reason": "tool_use"}

    class _BoundLLM:
        async def ainvoke(self, _msgs):
            return _FakeMessage()

    class _FakeLLM:
        def __init__(self, **_kw):
            pass

        def bind_tools(self, *_a, **_kw):
            return _BoundLLM()

    with patch("langchain_anthropic.ChatAnthropic", _FakeLLM), \
         patch("app.core.config.get_settings") as gs:
        gs.return_value.get_anthropic_answer_formatter_key.return_value = "sk-test"
        out = asyncio.run(fmt._invoke_llm("sys", "user", "goal_planning"))
    assert out == "You're an **Aggressive** investor."


# ---------------------------------------------------------------------------
# format_with_telemetry tests
# ---------------------------------------------------------------------------

class FormatWithTelemetryTests(unittest.TestCase):

    def _ctx(self):
        from unittest.mock import MagicMock
        ctx = MagicMock()
        ctx.user_question = "test question"
        ctx.conversation_history = []
        ctx.db = MagicMock()
        ctx.effective_user_id = uuid.uuid4()
        ctx.session_id = uuid.uuid4()
        return ctx

    def test_format_with_telemetry_returns_formatter_text_on_success(self):
        from app.domains.ai_engine.answer_formatter import format_with_telemetry
        with patch("app.domains.ai_engine.answer_formatter.formatter.format_answer",
                   new=AsyncMock(return_value="tailored")), \
             patch("app.domains.ai_engine.answer_formatter.formatter.record_ai_module_run",
                   new=AsyncMock(return_value=None)):
            text = asyncio.run(format_with_telemetry(
                ctx=self._ctx(),
                facts_pack={},
                body_prompt="b",
                module_name="x",
                action_mode="compute",
                profile={},
                build_fallback=lambda: "FALLBACK",
            ))
        self.assertEqual(text, "tailored")

    def test_format_with_telemetry_uses_fallback_on_formatter_failure(self):
        from app.domains.ai_engine.answer_formatter import (
            FormatterFailure,
            format_with_telemetry,
        )
        with patch("app.domains.ai_engine.answer_formatter.formatter.format_answer",
                   new=AsyncMock(side_effect=FormatterFailure("api_down"))), \
             patch("app.domains.ai_engine.answer_formatter.formatter.record_ai_module_run",
                   new=AsyncMock(return_value=None)):
            text = asyncio.run(format_with_telemetry(
                ctx=self._ctx(),
                facts_pack={},
                body_prompt="b",
                module_name="x",
                action_mode="compute",
                profile={},
                build_fallback=lambda: "FALLBACK",
            ))
        self.assertEqual(text, "FALLBACK")

    def test_format_with_telemetry_records_run_with_correct_columns_on_success(self):
        from app.domains.ai_engine.answer_formatter import format_with_telemetry
        captured = {}

        async def fake_record(*args, **kwargs):
            captured.update(kwargs)
            return uuid.uuid4()

        with patch("app.domains.ai_engine.answer_formatter.formatter.format_answer",
                   new=AsyncMock(return_value="ok")), \
             patch("app.domains.ai_engine.answer_formatter.formatter.record_ai_module_run",
                   side_effect=fake_record):
            asyncio.run(format_with_telemetry(
                ctx=self._ctx(),
                facts_pack={},
                body_prompt="b",
                module_name="rebalancing",
                action_mode="compute",
                profile={},
                build_fallback=lambda: "",
            ))
        self.assertTrue(captured.get("formatter_invoked"))
        self.assertTrue(captured.get("formatter_succeeded"))
        self.assertEqual(captured.get("module"), "rebalancing")
        self.assertEqual(captured.get("action_mode"), "compute")


# ---------------------------------------------------------------------------
# Extra (non-prose) tool fields — used by portfolio_query for its guardrail
# verdict and telemetry. A second PROSE field is still off the table.
# ---------------------------------------------------------------------------


def _fake_llm_returning(args: dict, captured: dict | None = None):
    """A ChatAnthropic stand-in whose forced tool returns ``args``."""
    class _FakeMessage:
        tool_calls = [{"name": "return_formatted_answer", "args": args}]
        response_metadata = {"stop_reason": "tool_use"}

    class _BoundLLM:
        async def ainvoke(self, _msgs):
            return _FakeMessage()

    class _FakeLLM:
        def __init__(self, **_kw):
            pass

        def bind_tools(self, tools, **_kw):
            if captured is not None:
                captured["schema"] = tools[0]["input_schema"]
            return _BoundLLM()

    return _FakeLLM


def _run_invoke(fake_llm, **kwargs):
    from app.domains.ai_engine.answer_formatter import formatter as fmt

    with patch("langchain_anthropic.ChatAnthropic", fake_llm), \
         patch("app.core.config.get_settings") as gs:
        gs.return_value.get_anthropic_answer_formatter_key.return_value = "sk-test"
        return asyncio.run(fmt._invoke_llm("sys", "user", "portfolio_query", **kwargs))


_EXTRA_FIELDS = {
    "guardrail_triggered": {"type": "boolean", "description": "d"},
    "path": {"type": ["string", "null"], "description": "d"},
}


def test_extra_tool_fields_reach_the_schema_and_their_values_come_back():
    captured: dict = {}
    extras: dict = {}
    out = _run_invoke(
        _fake_llm_returning(
            {"answer": "You hold ₹23.61 lakh.", "guardrail_triggered": False, "path": "P"},
            captured,
        ),
        extra_tool_fields=_EXTRA_FIELDS,
        extras_out=extras,
    )
    assert out == "You hold ₹23.61 lakh."
    assert extras == {"guardrail_triggered": False, "path": "P"}
    assert set(captured["schema"]["properties"]) == {"answer", "guardrail_triggered", "path"}
    assert captured["schema"]["required"] == ["answer"]   # only the answer is mandatory


def test_answer_stays_non_nullable_unless_the_caller_opts_in():
    captured: dict = {}
    _run_invoke(_fake_llm_returning({"answer": "hi"}, captured))
    assert captured["schema"]["properties"]["answer"]["type"] == "string"

    captured2: dict = {}
    _run_invoke(_fake_llm_returning({"answer": "hi"}, captured2), allow_empty_answer=True)
    assert captured2["schema"]["properties"]["answer"]["type"] == ["string", "null"]


def test_null_answer_is_a_failure_by_default_but_an_outcome_when_allowed():
    """Path X nulls `answer` on purpose — that must not trigger the fallback brief."""
    with pytest.raises(FormatterFailure, match="no_tool_call"):
        _run_invoke(_fake_llm_returning({"answer": None}))

    extras: dict = {}
    out = _run_invoke(
        _fake_llm_returning({"answer": None, "guardrail_triggered": True, "path": "X"}),
        extra_tool_fields=_EXTRA_FIELDS,
        extras_out=extras,
        allow_empty_answer=True,
    )
    assert out == ""
    assert extras["guardrail_triggered"] is True
