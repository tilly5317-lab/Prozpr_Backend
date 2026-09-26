"""Labeled eval for the AA detector's preference extraction (LIVE Haiku calls).

Run explicitly:
    RUN_AA_PREFERENCE_EVAL=1 .venv-mac/bin/python -m pytest \
        AI_Agents/tests/test_aa_preference_eval.py -m aa_preference_eval -v -s

Spec 2026-09-16 D5: a named asset class / fund category is a PREFERENCE; BARE
risk-appetite wording still moves `effective_risk_score`, because this domain
owns the risk profile. Rebalancing reads the same bare words as an equity
preference — that asymmetry is deliberate, so it is pinned on both sides.

Measured 2026-09-16: 13/13 (threshold 12), 23s. All nine preference phrasings
extracted; all four bare risk-appetite phrasings correctly stayed on the risk
score, so the D5 asymmetry holds.
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

from _eval_harness import run_suite  # noqa: E402

pytestmark = pytest.mark.aa_preference_eval

_ENABLED = bool(os.environ.get("RUN_AA_PREFERENCE_EVAL"))


@dataclass(frozen=True)
class Case:
    label: str
    question: str
    expect: str  # "preference" | "risk"
    targets: tuple = ()


CASES = [
    # A named class or category → preference_asks.
    Case("gold", "add some gold to my allocation", "preference", ("gold",)),
    Case("equity-up", "push my equity up a bit", "preference", ("equity",)),
    Case("equity-pct", "take my equity to 70%", "preference", ("equity",)),
    Case("no-smallcap", "I don't want any small cap funds", "preference", ("small_cap",)),
    Case("midcap-lean", "lean toward mid cap", "preference", ("mid_cap",)),
    Case("no-sector", "no sectoral or thematic funds please", "preference", ("sector",)),
    Case("drop-us", "drop the US funds", "preference", ("us_international",)),
    Case("young-equity", "I'm 28 so add more equity", "preference", ("equity",)),
    # "less debt" names a class, so it is a preference here — it used to be a
    # clarify example in the prompt (struck 2026-09-16).
    Case("less-debt", "less debt please", "preference", ("debt",)),
    # BARE risk appetite → the risk score, NOT a preference (D5).
    Case("bare-risk", "I can take more risk", "risk"),
    Case("bare-aggressive", "be more aggressive", "risk"),
    Case("bare-safer", "make it safer", "risk"),
    Case("bare-conservative", "I want to be more conservative", "risk"),
]


def _last_alloc():
    """`_detect_action` calls `_slim_snapshot(last_alloc.output_payload)` on its
    first line, so this argument cannot be None. An empty-but-shaped payload
    exercises the pure-question path. The risk score is read from
    allocation_result.client_summary.effective_risk_score."""
    from unittest.mock import MagicMock

    return MagicMock(
        output_payload={
            "allocation_result": {"client_summary": {"effective_risk_score": 5.5}}
        }
    )


def _runner(case: Case):
    import asyncio

    # A plain module, not a conftest, precisely so both the rebalancing tests
    # and an AI_Agents eval can build the same TurnContext (see its docstring).
    from app.domains.asset_allocation.services.aa_engine.chat import _detect_action
    from app.domains.rebalancing.tests.detector_ctx import make_detector_ctx

    return asyncio.run(
        _detect_action(_last_alloc(), make_detector_ctx(case.question))
    )


def _grader(case: Case, action):
    asks = action.preference_asks or []
    if case.expect == "preference":
        if not asks:
            return False, f"no preference_asks (mode={action.mode})"
        got = {a.target for a in asks}
        missing = set(case.targets) - got
        return (not missing), f"targets={sorted(got)} missing={sorted(missing)}"
    if asks:
        return False, f"risk ask extracted as a preference: {[a.target for a in asks]}"
    if action.mode == "counterfactual_explore":
        ok = "effective_risk_score" in (action.overrides or {})
        return ok, f"overrides={action.overrides}"
    return action.mode == "clarify", f"mode={action.mode}"


@pytest.mark.skipif(not _ENABLED, reason="live eval; set RUN_AA_PREFERENCE_EVAL=1")
def test_aa_preference_extraction():
    report = run_suite(
        suite="aa-preference-extraction",
        cases=CASES,
        runner=_runner,
        grader=_grader,
        threshold=len(CASES) - 1,
    )
    print(report.summary())
    assert report.passes >= report.threshold, report.summary()
