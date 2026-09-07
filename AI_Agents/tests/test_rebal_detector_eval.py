"""Labeled eval for the rebalancing action detector (LIVE Haiku calls).

Run explicitly:
    RUN_REBAL_DETECTOR_EVAL=1 .venv-mac/bin/python -m pytest \
        AI_Agents/tests/test_rebal_detector_eval.py -m rebal_detector_eval -v -s

Gated by RUN_REBAL_DETECTOR_EVAL=1 (repo convention for live-LLM suites —
key presence alone must not trigger API spend). The Anthropic key comes from
settings/.env as in production.

Two vocabularies: "existing" is the no-regression floor, "s2-pref" grades the
S1 wire-format intent the lexicon builds from the detector's `preference_asks`.
"""

from __future__ import annotations

import asyncio
import dataclasses
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

_ENABLED = bool(os.environ.get("RUN_REBAL_DETECTOR_EVAL"))


@dataclass(frozen=True)
class Case:
    label: str
    question: str
    expect_mode: str
    vocab: str = "existing"                    # "existing" | "s2-pref"
    expect_override_keys: frozenset = field(default_factory=frozenset)
    expect_clarify: bool = False
    expect_intent: dict | None = None          # S1 wire format after the lexicon
    expect_unmapped: bool = False
    expect_fund_count: int | None = None
    history: tuple = ()                        # (role, content) pairs, oldest first


def _sub(**kw):
    return {"subgroups": kw}


def _cls(cls, direction, target_pct=None):
    ac = {"class": cls, "direction": direction}
    if target_pct is not None:
        ac["target_pct"] = float(target_pct)
    return {"asset_class": ac}


CASES = [
    # ---- existing vocabulary (the no-regression floor) ----
    Case("narrate-why-sell", "why are you selling my HDFC fund?", "narrate"),
    Case("educate-exit-load", "what is exit load?", "educate"),
    Case("cf-tax-rate", "what if my tax rate were 20%?", "counterfactual_explore",
         expect_override_keys=frozenset({"effective_tax_rate"})),
    Case("cf-extra-cash", "what if I had 2 lakh more to deploy?",
         "counterfactual_explore", expect_override_keys=frozenset({"additional_cash_inr"})),
    Case("consolidate-count", "can you do this with just 4 funds?", "consolidate"),
    Case("consolidate-newmoney", "put the new money only in index funds", "consolidate"),
    Case("redirect-lock", "don't sell my HDFC Top 100", "redirect"),
    Case("compute-rerun", "rebalance again with my latest holdings", "compute"),
    Case("exclude-elss", "nothing with a lock-in please", "consolidate", vocab="s2-pref"),
    Case("named-include", "use Parag Parikh Flexi Cap instead", "narrate", vocab="s2-pref"),
    Case("named-why-not", "why didn't you pick Quant Small Cap?", "narrate", vocab="s2-pref"),
    Case("contradiction", "only debt funds but add more mid cap", "clarify",
         vocab="s2-pref", expect_clarify=True),
    Case("vague-safer", "make it safer", "counterfactual_explore", vocab="s2-pref",
         expect_intent=_cls("debt", "more")),
    # ---- S2 preference vocabulary → the S1 wire format ----
    Case("s2-more-equity", "increase my equity exposure", "counterfactual_explore",
         vocab="s2-pref", expect_intent=_cls("equity", "more")),
    Case("s2-equity-heavy", "make it equity heavy", "counterfactual_explore",
         vocab="s2-pref", expect_intent=_cls("equity", "heavy")),
    Case("s2-equity-70", "take my equity exposure to 70%", "counterfactual_explore",
         vocab="s2-pref", expect_intent=_cls("equity", "target", 70)),
    Case("s2-hundred-equity", "make it 100% equity", "counterfactual_explore",
         vocab="s2-pref", expect_intent=_cls("equity", "target", 100)),
    Case("s2-only-equity", "I only want to invest in equity funds", "counterfactual_explore",
         vocab="s2-pref", expect_intent=_cls("equity", "target", 100)),
    Case("s2-no-debt", "drop debt entirely", "counterfactual_explore",
         vocab="s2-pref", expect_intent=_cls("debt", "none")),
    Case("s2-more-gold", "add a bit of gold as a hedge", "counterfactual_explore",
         vocab="s2-pref", expect_intent=_sub(gold_commodities="more")),
    Case("s2-gold-30", "make gold 30%", "counterfactual_explore",
         vocab="s2-pref", expect_intent=_sub(gold_commodities=30.0)),
    Case("s2-more-midcap", "I want more mid cap in this plan", "counterfactual_explore",
         vocab="s2-pref", expect_intent=_sub(medium_beta_equities="more")),
    Case("s2-smallcap-heavy", "make it small-cap heavy", "counterfactual_explore",
         vocab="s2-pref", expect_intent=_sub(high_beta_equities="heavy")),
    Case("s2-less-largecap", "a bit less large cap", "counterfactual_explore",
         vocab="s2-pref", expect_intent=_sub(low_beta_equities="less")),
    Case("s2-drop-us", "drop the US funds", "counterfactual_explore",
         vocab="s2-pref", expect_intent=_sub(us_equities="none")),
    Case("s2-no-sectoral", "no sectoral funds", "counterfactual_explore",
         vocab="s2-pref", expect_intent=_sub(sector_equities="none")),
    Case("s2-more-value", "more value funds", "counterfactual_explore",
         vocab="s2-pref", expect_intent=_sub(value_equities="more")),
    Case("s2-multi-facet", "more equity and drop the US funds", "counterfactual_explore",
         vocab="s2-pref",
         expect_intent={**_cls("equity", "more"), **_sub(us_equities="none")}),
    Case("s2-stacked-count", "only equity, more mid cap, max 4 funds",
         "counterfactual_explore", vocab="s2-pref",
         expect_intent={**_cls("equity", "target", 100), **_sub(medium_beta_equities="more")},
         expect_fund_count=4),
    Case("s2-aggressive-age", "I'm 28 and want to be aggressive — push my equity up",
         "counterfactual_explore", vocab="s2-pref", expect_intent=_cls("equity", "more")),
    Case("s2-adjust-prior", "add gold to that", "counterfactual_explore", vocab="s2-pref",
         expect_intent=_sub(gold_commodities="more"),
         history=(("user", "show me 100% equity"),
                  ("assistant", "Here is the 100% equity version… Want me to save this as your preference?"))),
    Case("s2-unmapped", "only banking funds please", "counterfactual_explore",
         vocab="s2-pref", expect_unmapped=True),
    # ---- Task 5 review: thin boundary between consolidate and preference_asks ----
    Case("consolidate-banking-weight", "at least 30% in banking funds", "consolidate",
         vocab="existing"),
]


def _runner(case: Case):
    from app.domains.rebalancing.services.rebal_engine.chat import _detect_rebal_action
    from app.domains.rebalancing.tests.detector_ctx import (
        make_detector_ctx,
        make_last_run,
    )

    ctx = make_detector_ctx(case.question)
    ctx = dataclasses.replace(
        ctx,
        conversation_history=[{"role": r, "content": c} for r, c in case.history],
    )
    return asyncio.run(_detect_rebal_action(make_last_run(), ctx))


def _grader(case: Case, action):
    from app.domains.profile.services.preference_lexicon import build_intent

    if action.mode != case.expect_mode:
        return False, f"mode={action.mode} want={case.expect_mode}"
    got_keys = frozenset((action.overrides or {}).keys())
    if case.expect_override_keys and not case.expect_override_keys <= got_keys:
        return False, f"override keys={sorted(got_keys)}"
    if case.expect_clarify and not action.clarification_question:
        return False, "no clarification_question"
    intent, unmapped = build_intent(action.preference_asks)
    if case.expect_intent is not None and intent != case.expect_intent:
        return False, f"intent={intent} want={case.expect_intent}"
    if case.expect_unmapped and not unmapped:
        return False, f"expected an unmapped ask, got intent={intent}"
    if case.expect_fund_count is not None and action.target_fund_count != case.expect_fund_count:
        return False, f"target_fund_count={action.target_fund_count}"
    return True, ""


@pytest.mark.skipif(not _ENABLED, reason="live eval; set RUN_REBAL_DETECTOR_EVAL=1")
def test_existing_vocabulary_floor():
    existing = [c for c in CASES if c.vocab == "existing"]
    report = run_suite(suite="rebal-detector-existing", cases=existing,
                       runner=_runner, grader=_grader,
                       threshold=len(existing) - 1)   # allow 1 flake
    print(report.summary())
    assert report.passes >= report.threshold, report.summary()


@pytest.mark.skipif(not _ENABLED, reason="live eval; set RUN_REBAL_DETECTOR_EVAL=1")
def test_preference_vocabulary():
    prefs = [c for c in CASES if c.vocab == "s2-pref"]
    report = run_suite(suite="rebal-detector-preference", cases=prefs,
                       runner=_runner, grader=_grader, threshold=len(prefs) - 3)
    print(report.summary())
    assert report.passes >= report.threshold, report.summary()
