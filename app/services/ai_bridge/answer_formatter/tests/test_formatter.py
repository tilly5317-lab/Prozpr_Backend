"""Tests for the shared answer_formatter — prompt assembly + types + fallback."""

from __future__ import annotations

import pytest

from app.services.ai_bridge.answer_formatter import (
    FORMATTER_HOUSE_STYLE,
    FormatterFailure,
    assemble_prompt,
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
    assert "never recommend" in text or "no specific fund" in text
    assert "never invent" in text or "do not invent" in text


def test_formatter_failure_is_an_exception():
    err = FormatterFailure("boom")
    assert isinstance(err, Exception)
    assert "boom" in str(err)


# ---------------------------------------------------------------------------
# LLM call tests (Task 3)
# ---------------------------------------------------------------------------

import asyncio
from unittest.mock import AsyncMock, patch

from app.services.ai_bridge.answer_formatter import format_answer


def test_format_answer_returns_text_on_success():
    with patch(
        "app.services.ai_bridge.answer_formatter.formatter._invoke_llm",
        new=AsyncMock(return_value="Here's your tailored answer."),
    ):
        out = asyncio.run(format_answer(
            question="?", action_mode="narrate", module_name="x",
            facts_pack={"k": 1}, body_prompt="b", history=[], profile={},
        ))
    assert out == "Here's your tailored answer."


def test_format_answer_raises_formatter_failure_on_empty_response():
    with patch(
        "app.services.ai_bridge.answer_formatter.formatter._invoke_llm",
        new=AsyncMock(return_value=""),
    ):
        with pytest.raises(FormatterFailure):
            asyncio.run(format_answer(
                question="?", action_mode="narrate", module_name="x",
                facts_pack={}, body_prompt="b", history=[], profile={},
            ))


def test_format_answer_raises_formatter_failure_on_llm_exception():
    with patch(
        "app.services.ai_bridge.answer_formatter.formatter._invoke_llm",
        new=AsyncMock(side_effect=RuntimeError("api down")),
    ):
        with pytest.raises(FormatterFailure):
            asyncio.run(format_answer(
                question="?", action_mode="narrate", module_name="x",
                facts_pack={}, body_prompt="b", history=[], profile={},
            ))
