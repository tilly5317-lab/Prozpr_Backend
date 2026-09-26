"""LIVE eval gate for Logics-doc grounding in the shared answer formatter.

Run BEFORE merging changes to the Logics reference docs
(``AI_Agents/Reference_docs/Logics_reference_docs/``), the formatter's
LOGIC_REFERENCE block, or its no-unpublished-numbers guardrail:

    ENABLE_LLM_SMOKE=1 .venv-mac/bin/python -m pytest -m real_llm \
        app/domains/ai_engine/tests/eval_gate/ -s

Exercises the PRODUCTION composition — house style + the module's real
formatter body + the real thesis doc via ``get_logic_reference`` — with a
minimal facts pack, and asserts the answer is grounded in the doc rather
than the LLM's general knowledge. 4 Haiku calls. Not run in CI.

A failure here usually means one of:
  - a thesis doc was edited and no longer answers its own FAQ (doc drift),
  - the LOGIC_REFERENCE block or guardrail line was weakened,
  - the formatter started inventing policy numbers again.
"""

from __future__ import annotations

import asyncio
import os
import re

import pytest

pytestmark = pytest.mark.real_llm

if os.getenv("ENABLE_LLM_SMOKE") != "1":
    pytest.skip(
        "Set ENABLE_LLM_SMOKE=1 to run the live prompt eval gate",
        allow_module_level=True,
    )

import app.all_models  # noqa: F401
import app.domains.ai_engine  # noqa: F401
from app.domains.ai_engine.answer_formatter import format_answer
from app.domains.ai_engine.logic_docs import get_logic_reference

# Imported lazily-in-order (after app.domains.ai_engine) — rebal chat is not
# re-exported from its package __init__ to avoid a circular import.
from app.domains.rebalancing.services.rebal_engine.chat import _REBAL_FORMATTER_BODY
from app.domains.asset_allocation.services.aa_engine.chat import _AA_FORMATTER_BODY

_PROFILE = {"first_name": "Asha"}
_PERCENT_RE = re.compile(r"\b\d+(?:\.\d+)?\s*%")


def _ask(*, question: str, module: str, body: str, facts: dict) -> str:
    return asyncio.run(
        format_answer(
            question=question,
            action_mode="educate",
            module_name=module,
            facts_pack=facts,
            body_prompt=body,
            history=[],
            profile=_PROFILE,
            logic_reference=get_logic_reference(module),
        )
    )


def test_rebal_sell_order_grounds_in_current_thesis():
    """The sell-order story must be the doc's (LT-first) and must not resurrect
    the removed exit-load-window tiers."""
    answer = _ask(
        question="why are you selling my oldest units first?",
        module="rebalancing",
        body=_REBAL_FORMATTER_BODY,
        facts={"summary": {"total_sell_inr": 100000, "total_buy_inr": 100000}},
    )
    lowered = answer.lower()
    assert "long-term" in lowered or "long term" in lowered, answer
    assert "exit-load window" not in lowered and "exit load window" not in lowered, answer


def test_rebal_threshold_probe_refuses_to_invent_a_number():
    """The docs deliberately omit the drift threshold; the guardrail requires
    declining rather than estimating. Any percentage other than the doc-stated
    12.5% LTCG rate is an invented policy number."""
    answer = _ask(
        question="what exact drift threshold percentage do you use before rebalancing a fund?",
        module="rebalancing",
        body=_REBAL_FORMATTER_BODY,
        facts={"summary": {"total_sell_inr": 0, "total_buy_inr": 0}},
    )
    invented = [m for m in _PERCENT_RE.findall(answer) if "12.5" not in m]
    assert not invented, f"invented policy number(s) {invented} in: {answer}"


def test_aa_split_question_grounds_in_risk_and_goals():
    answer = _ask(
        question="how do you decide how much equity versus debt I should hold?",
        module="asset_allocation",
        body=_AA_FORMATTER_BODY,
        facts={"asset_class_mix_pct": {"equity": 55, "debt": 35, "others": 10}},
    )
    lowered = answer.lower()
    assert "risk" in lowered, answer
    assert "goal" in lowered or "horizon" in lowered, answer


def test_aa_emergency_fund_answer_matches_production_reality():
    """Doc drift guard: the thesis now says the emergency carve-out is off by
    default (what-if only). The answer must not claim the plan reserved one."""
    answer = _ask(
        question="how does my plan handle an emergency fund?",
        module="asset_allocation",
        body=_AA_FORMATTER_BODY,
        facts={"asset_class_mix_pct": {"equity": 55, "debt": 35, "others": 10}},
    )
    lowered = answer.lower()
    assert "reserved before any other bucket" not in lowered, answer
    assert "what-if" in lowered or "what if" in lowered or "not carve" in lowered or (
        "default" in lowered
    ), answer
