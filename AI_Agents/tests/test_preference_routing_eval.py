"""Labeled eval for classifier stickiness on preference asks (LIVE Haiku calls).

Run explicitly:
    RUN_INTENT_ROUTING_EVAL=1 .venv-mac/bin/python -m pytest \
        AI_Agents/tests/test_preference_routing_eval.py -m intent_routing_eval -v -s

Mid-conversation preference asks must stay in the active rebalancing flow so
they reach the S2 extractor; a fresh policy QUESTION must still leave for
asset_allocation.

Measured 2026-09-05: 14/14 (threshold 13/14; no prompt change needed).
Re-measured 2026-09-16 with 8 record-vs-plan cases added: 19/19 (threshold 18).
That run grades the new routing-edge-case text: "reset my preferences" moved from
out_of_scope to portfolio_query, while a ONE-CATEGORY ask stays with the active
plan even when the customer says the word "preference" — which matters because
"remove my small-cap preference" must NOT become {small_cap, none}, the opposite
of what they asked for.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

_TESTS_DIR = Path(__file__).resolve().parent
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))
_SRC = _TESTS_DIR.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from _eval_harness import run_suite  # noqa: E402

pytestmark = pytest.mark.intent_routing_eval

_ENABLED = bool(os.environ.get("RUN_INTENT_ROUTING_EVAL"))

_PLAN_HISTORY = (
    ("user", "rebalance my portfolio"),
    ("assistant", "Here is your rebalancing plan: 3 sells, 4 buys, landing at 62% equity…"),
)


@dataclass(frozen=True)
class Case:
    label: str
    question: str
    expect_intent: str
    active_intent: str | None = "rebalancing"
    history: tuple = _PLAN_HISTORY


CASES = [
    Case("stick-more-equity", "I want more equity", "rebalancing"),
    Case("stick-gold-hedge", "add a bit of gold as a hedge", "rebalancing"),
    Case("stick-safer", "make it safer", "rebalancing"),
    Case("stick-midcap", "lean toward mid cap", "rebalancing"),
    Case("stick-drop-us", "drop the US funds", "rebalancing"),
    Case("stick-hundred", "show me 100% equity", "rebalancing"),
    Case("stick-aggressive-age", "I'm 28 and want to be aggressive — push my equity up", "rebalancing"),
    Case("stick-named-fund", "why not put everything in Quant Small Cap?", "rebalancing"),
    # over-correction guards
    Case("keep-policy-question", "should I be more aggressive given my age?", "asset_allocation"),
    Case("keep-fresh-aa", "what allocation is right for someone my age?", "asset_allocation",
         active_intent=None, history=()),
    Case("keep-portfolio-readout", "what is my risk profile?", "portfolio_query"),
    # ---- The preference RECORD vs the plan in view (spec 2026-09-16 D6) ----
    # Measured 2026-09-16 BEFORE the prompt line: readouts already landed in
    # portfolio_query (0.85 cold / 0.92 mid); "reset my preferences" came back
    # out_of_scope (0.85) — that is the row the new line fixes.
    Case("record-readout-mid", "what preferences do I have set?", "portfolio_query"),
    Case("record-readout-cold", "what are my investment preferences?",
         "portfolio_query", active_intent=None, history=()),
    Case("record-exclusions-cold", "which fund categories am I excluding?",
         "portfolio_query", active_intent=None, history=()),
    Case("record-reset", "reset my preferences", "portfolio_query"),
    Case("record-clear-cold", "clear my saved preferences", "portfolio_query",
         active_intent=None, history=()),
    # The plan in view keeps its intent — a ONE-CATEGORY ask does not become a
    # record question just because the customer says "preference".
    Case("plan-attribution", "is this plan based on my preferences?", "rebalancing"),
    Case("plan-targeted-undo", "remove my small cap preference", "rebalancing"),
    Case("plan-drop-gold-pref", "drop my gold preference", "rebalancing"),
]


def _runner(case: Case):
    from intent_classifier.classifier import IntentClassifier
    from intent_classifier.models import ClassificationInput, ConversationMessage, Intent

    from app.core.config import get_settings

    clf = IntentClassifier(api_key=get_settings().get_anthropic_intent_classifier_key())
    return clf.classify(ClassificationInput(
        customer_question=case.question,
        conversation_history=[ConversationMessage(role=r, content=c) for r, c in case.history],
        active_intent=Intent(case.active_intent) if case.active_intent else None,
    ))


def _grader(case: Case, result):
    if result.intent.value != case.expect_intent:
        return False, f"intent={result.intent.value} want={case.expect_intent}"
    return True, ""


@pytest.mark.skipif(not _ENABLED, reason="live eval; set RUN_INTENT_ROUTING_EVAL=1")
def test_preference_asks_stay_in_the_active_flow():
    report = run_suite(suite="intent-routing-preferences", cases=CASES,
                       runner=_runner, grader=_grader, threshold=len(CASES) - 1)
    print(report.summary())
    assert report.passes >= report.threshold, report.summary()
