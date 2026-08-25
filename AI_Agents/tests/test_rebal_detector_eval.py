"""Labeled eval for the rebalancing action detector (LIVE Haiku calls).

Run explicitly:
    RUN_REBAL_DETECTOR_EVAL=1 .venv-mac/bin/python -m pytest \
        AI_Agents/tests/test_rebal_detector_eval.py -m rebal_detector_eval -v -s

Gated by RUN_REBAL_DETECTOR_EVAL=1 (repo convention for live-LLM suites —
key presence alone must not trigger API spend). The Anthropic key comes from
settings/.env as in production.

Cases marked vocab="v1-pref" are EXPECTED TO FAIL until the preference
vocabulary ships (plan Task 8); the existing-vocab threshold is the
no-regression floor recorded before any detector change.
"""

from __future__ import annotations

import asyncio
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pytest

# Same pattern as test_intent_classifier.py: make the sibling harness importable
# under both `pytest` (rootdir = repo root) and `unittest AI_Agents.tests.*`.
_TESTS_DIR = Path(__file__).resolve().parent
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))

from _eval_harness import run_suite  # noqa: E402

pytestmark = pytest.mark.rebal_detector_eval

BASELINE_NOTE = (
    "2026-08-24 pre-vocabulary baseline: existing-vocab 8/8; v1-pref 4/12. "
    "Post-vocabulary (same day, plan Task 8): existing-vocab 8/8; v1-pref 12/12."
)

_ENABLED = bool(os.environ.get("RUN_REBAL_DETECTOR_EVAL"))


@dataclass(frozen=True)
class Case:
    label: str
    question: str
    expect_mode: str
    vocab: str = "existing"                    # "existing" | "v1-pref"
    expect_override_keys: frozenset = field(default_factory=frozenset)
    expect_clarify: bool = False


CASES = [
    # ---- existing vocabulary (the no-regression floor) ----
    Case("narrate-why-sell", "why are you selling my HDFC fund?", "narrate"),
    Case("educate-exit-load", "what is exit load?", "educate"),
    Case("cf-tax-rate", "what if my tax rate were 20%?", "counterfactual_explore",
         expect_override_keys=frozenset({"effective_tax_rate"})),
    Case("cf-extra-cash", "what if I had 2 lakh more to deploy?",
         "counterfactual_explore", expect_override_keys=frozenset({"additional_cash_inr"})),
    Case("consolidate-count", "can you do this with just 4 funds?", "consolidate"),
    Case("consolidate-category", "put the new money only in large cap", "consolidate"),
    Case("redirect-lock", "don't sell my HDFC Top 100", "redirect"),
    Case("compute-rerun", "rebalance again with my latest holdings", "compute"),
    # ---- v1 preference vocabulary (plan Task 8 makes these pass) ----
    Case("tilt-delta", "increase my equity by 10 percent", "counterfactual_explore",
         vocab="v1-pref"),
    Case("tilt-absolute", "take my equity exposure to 70%", "counterfactual_explore",
         vocab="v1-pref"),
    Case("tilt-no-number", "increase my equity exposure", "counterfactual_explore",
         vocab="v1-pref"),
    Case("scope-only-equity", "I only want to invest in equity funds",
         "counterfactual_explore", vocab="v1-pref"),
    Case("weight-mid-cap", "I want more mid cap in this plan", "consolidate",
         vocab="v1-pref"),
    Case("exclude-elss", "nothing with a lock-in please", "consolidate",
         vocab="v1-pref"),
    Case("named-include", "use Parag Parikh Flexi Cap instead", "narrate",
         vocab="v1-pref"),   # Phase 1: honest "coming later" + demand telemetry
    Case("named-why-not", "why didn't you pick Quant Small Cap?", "narrate",
         vocab="v1-pref"),
    Case("stacked", "only equity, and more mid cap, max 4 funds", "consolidate",
         vocab="v1-pref"),
    Case("contradiction", "only debt funds but add more mid cap", "clarify",
         vocab="v1-pref", expect_clarify=True),
    Case("vague-safer", "make it safer", "clarify", vocab="v1-pref",
         expect_clarify=True),
    Case("oov-esg", "only ESG funds please", "redirect", vocab="v1-pref"),
]


def _runner(case: Case):
    from app.domains.rebalancing.services.rebal_engine.chat import _detect_rebal_action
    from app.domains.rebalancing.services.rebal_engine.tests.detector_ctx import (
        make_detector_ctx,
        make_last_run,
    )

    return asyncio.run(
        _detect_rebal_action(make_last_run(), make_detector_ctx(case.question))
    )


def _grader(case: Case, action):
    if action.mode != case.expect_mode:
        return False, f"mode={action.mode} want={case.expect_mode}"
    got_keys = frozenset((action.overrides or {}).keys())
    if case.expect_override_keys and not case.expect_override_keys <= got_keys:
        return False, f"override keys={sorted(got_keys)}"
    if case.expect_clarify and not action.clarification_question:
        return False, "no clarification_question"
    return True, ""


@pytest.mark.skipif(not _ENABLED, reason="live eval; set RUN_REBAL_DETECTOR_EVAL=1")
def test_existing_vocabulary_floor():
    existing = [c for c in CASES if c.vocab == "existing"]
    report = run_suite(suite="rebal-detector-existing", cases=existing,
                       runner=_runner, grader=_grader,
                       threshold=len(existing) - 1)   # allow 1 flake in 8
    print(report.summary())
    assert report.passes >= report.threshold, report.summary()


@pytest.mark.skipif(not _ENABLED, reason="live eval; set RUN_REBAL_DETECTOR_EVAL=1")
def test_v1_preference_vocabulary():
    prefs = [c for c in CASES if c.vocab == "v1-pref"]
    # Vocabulary shipped (12/12 on 2026-08-24); len-2 allows 2 flakes in 12.
    report = run_suite(suite="rebal-detector-v1pref", cases=prefs,
                       runner=_runner, grader=_grader, threshold=len(prefs) - 2)
    print(report.summary())
    assert report.passes >= report.threshold, report.summary()
