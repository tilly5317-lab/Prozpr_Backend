"""LIVE eval gate for the intent classifier prompt (audit F10).

Run BEFORE merging any change to ``intent_classifier/prompts.py``,
``classifier.py``'s schema, or the history scrub:

    ENABLE_LLM_SMOKE=1 .venv-mac/bin/python -m pytest -m real_llm \
        app/domains/ai_engine/tests/eval_gate/ -s

Exercises the PRODUCTION path — ``classify_user_message`` (history scrub +
Anthropic classifier + rebalancing keyword override + fallback) — against the
pinned golden set in ``golden_cases.py``. ~65 Haiku calls, ≈$0.10, a few
minutes. Not run in CI (no API key there; see the ``real_llm`` marker).
"""

from __future__ import annotations

import asyncio
import os
import unittest

import pytest

pytestmark = pytest.mark.real_llm

if os.getenv("ENABLE_LLM_SMOKE") != "1":
    pytest.skip(
        "Set ENABLE_LLM_SMOKE=1 to run the live prompt eval gate",
        allow_module_level=True,
    )

import app.all_models  # noqa: F401
import app.domains.ai_engine  # noqa: F401  (init order — see engine circular import)
from app.domains.intent_classifier.services.intent_classifier_engine import (
    classify_user_message,
)
from AI_Agents.tests._eval_harness import run_suite

from .golden_cases import INTENT_CASES, INTENT_THRESHOLD, IntentCase


def _run_case(case: IntentCase):
    history = [{"role": role, "content": content} for role, content in case.history]
    result = asyncio.run(
        classify_user_message(
            customer_question=case.question,
            conversation_history=history,
            active_intent=case.active_intent,
        )
    )
    tools = frozenset(t.value for t in (result.tools_needed or ()))
    return result.intent.value, tools


def _grade(case: IntentCase, got) -> tuple[bool, str]:
    intent, tools = got
    if intent not in case.expected:
        return False, f"intent: expected {sorted(case.expected)}, got {intent!r}"
    if case.expected_tools is not None and tools != case.expected_tools:
        return False, f"tools: expected {sorted(case.expected_tools)}, got {sorted(tools)}"
    return True, ""


class IntentClassifierEvalGate(unittest.TestCase):
    def test_intent_golden_set(self) -> None:
        report = run_suite(
            suite="intent_classifier_golden",
            cases=INTENT_CASES,
            runner=_run_case,
            grader=_grade,
            threshold=INTENT_THRESHOLD,
        )
        report.assert_threshold(self)


if __name__ == "__main__":
    unittest.main()
