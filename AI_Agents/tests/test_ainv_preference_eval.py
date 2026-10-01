"""Labeled eval for the AINV deploy-request extractor's phrasing coverage
(LIVE Haiku calls).

Run explicitly:
    RUN_AINV_PREFERENCE_EVAL=1 .venv-mac/bin/python -m pytest \
        AI_Agents/tests/test_ainv_preference_eval.py -m ainv_preference_eval -v -s \
        -p no:cacheprovider

Grades amount (float equality), cadence (``Cadence`` value), and — for the
seven cases that carry preference asks — the intent produced by
``build_intent`` piped through ``_route_sole_class_subgroups`` (the same
routing the AINV what-if handler applies). The focus-category case checks
``raw_category`` is set with no asks; the unmapped case checks a non-empty
unmapped list.

Measured 2026-09-05: 9/9 (threshold 8/9; no prompt change needed).
"""

from __future__ import annotations

import asyncio
import os
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

_TESTS_DIR = Path(__file__).resolve().parent
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))

from _eval_harness import run_suite  # noqa: E402

pytestmark = pytest.mark.ainv_preference_eval

_ENABLED = bool(os.environ.get("RUN_AINV_PREFERENCE_EVAL"))


@dataclass(frozen=True)
class Case:
    label: str
    question: str
    expect_amount: float | None
    expect_cadence: str
    expect_intent: dict | None = None
    expect_focus_category: bool = False
    expect_unmapped: bool = False


CASES = [
    Case(
        "small-cap-heavy-sip",
        "start a 25k SIP, mostly small cap",
        25000.0, "sip_monthly",
        expect_intent={"subgroups": {"high_beta_equities": "heavy"}},
    ),
    Case(
        "no-us-funds",
        "invest 5L but no US funds",
        500000.0, "lumpsum",
        expect_intent={"subgroups": {"us_equities": "none"}},
    ),
    Case(
        "hundred-pct-equity",
        "2L, 100% equity",
        200000.0, "lumpsum",
        expect_intent={"asset_class": {"class": "equity", "direction": "target", "target_pct": 100.0}},
    ),
    Case(
        "bit-more-gold",
        "10k a month, a bit more gold",
        10000.0, "sip_monthly",
        # gold_commodities is the sole settable subgroup of "others" -> routed to asset_class.
        expect_intent={"asset_class": {"class": "others", "direction": "more"}},
    ),
    Case(
        "keep-it-safe",
        "put 3 lakh in, keep it safe",
        300000.0, "lumpsum",
        expect_intent={"asset_class": {"class": "debt", "direction": "more"}},
    ),
    Case(
        "which-gold-fund",
        "which gold fund should I buy?",
        None, "lumpsum",
        expect_focus_category=True,
    ),
    Case(
        "plain-invest-1l",
        "invest 1 lakh",
        100000.0, "lumpsum",
    ),
    Case(
        "banking-funds-unmapped",
        "50k SIP only in banking funds",
        50000.0, "sip_monthly",
        expect_unmapped=True,
    ),
    Case(
        "more-equity-drop-sector",
        "5L, more equity and drop the sector funds",
        500000.0, "lumpsum",
        expect_intent={
            "asset_class": {"class": "equity", "direction": "more"},
            "subgroups": {"sector_equities": "none"},
        },
    ),
]


def _runner(case: Case):
    from app.domains.additional_investment.services.ainv_engine.chat import (
        extract_deploy_request,
    )

    return asyncio.run(extract_deploy_request(case.question, history=[]))


def _grader(case: Case, result):
    from app.domains.profile.services.preference_lexicon import build_intent
    from app.domains.profile.services.preference_save_service import (
        _route_sole_class_subgroups,
    )

    amount, cadence, raw_category, asks = result

    if amount != case.expect_amount:
        return False, f"amount={amount!r} want={case.expect_amount!r}"
    if cadence.value != case.expect_cadence:
        return False, f"cadence={cadence.value} want={case.expect_cadence}"

    if case.expect_focus_category:
        if not raw_category:
            return False, "raw_category not set"
        if asks:
            return False, f"expected no asks, got {asks!r}"
        return True, ""

    if case.expect_unmapped:
        intent, unmapped = build_intent(asks)
        if not unmapped:
            return False, f"expected a non-empty unmapped list, got intent={intent} unmapped={unmapped}"
        return True, ""

    if case.expect_intent is None:
        if asks:
            return False, f"expected no preference asks, got {asks!r}"
        return True, ""

    intent, unmapped = build_intent(asks)
    intent = _route_sole_class_subgroups(intent)
    if intent != case.expect_intent:
        return False, f"intent={intent} want={case.expect_intent} (unmapped={unmapped})"
    return True, ""


@pytest.mark.skipif(not _ENABLED, reason="live eval; set RUN_AINV_PREFERENCE_EVAL=1")
def test_ainv_extractor_phrasing_coverage():
    report = run_suite(suite="ainv-preference-extraction", cases=CASES,
                       runner=_runner, grader=_grader, threshold=len(CASES) - 1)
    print(report.summary())
    assert report.passes >= report.threshold, report.summary()
