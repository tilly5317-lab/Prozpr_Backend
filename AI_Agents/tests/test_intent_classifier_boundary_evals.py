"""Live boundary evals: intent_classifier intent boundaries.

Migrated to use the shared eval harness (AI_Agents/tests/_eval_harness.py).
Behavior preserved: same 14 cases, same threshold of 12, same env-skip when
ANTHROPIC_API_KEY is missing.

Spec: docs/superpowers/specs/2026-05-02-shared-eval-harness-design.md

Run manually:
    ANTHROPIC_API_KEY=sk-... python -m pytest \
        AI_Agents/tests/test_intent_classifier_boundary_evals.py -v

Note on env-skip: `unset ANTHROPIC_API_KEY` alone is NOT sufficient to take
this suite offline. `intent_classifier/classifier.py` calls `load_dotenv()` at
import time, which puts the key back from a project-level `.env`. For a true
offline run use either:
    mv .env .env.bak                              # temporarily move .env aside
    ANTHROPIC_API_KEY= python -m pytest ...       # explicit empty string
"""
from __future__ import annotations

import os
import sys
import unittest
from dataclasses import dataclass

# Make AI_Agents/src importable when running from the repo root.
_HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(_HERE, "..", "src"))

from intent_classifier import (  # noqa: E402
    ClassificationInput,
    ClassificationResult,
    Intent,
    IntentClassifier,
)
from _eval_harness import run_suite  # noqa: E402


@dataclass(frozen=True)
class IntentCase:
    label: str
    question: str
    expected: Intent


CASES: list[IntentCase] = [
    # Pure feasibility (no money hook) → goal_planning
    IntentCase("feasibility-only-retirement",
               "I want to retire in 15 years with 5 crore — is that possible?",
               Intent.GOAL_PLANNING),
    IntentCase("feasibility-only-trajectory",
               "Will my current SIP be enough to hit 2 crore by 2040?",
               Intent.GOAL_PLANNING),
    IntentCase("feasibility-only-house",
               "Can I afford a 1cr house down-payment in 7 years?",
               Intent.GOAL_PLANNING),

    # Required savings → goal_planning
    IntentCase("required-savings-college",
               "How much should I save each month for my daughter's college in 10 years?",
               Intent.GOAL_PLANNING),
    IntentCase("required-savings-retirement",
               "How much do I need to invest monthly to retire with 5 crore in 20 years?",
               Intent.GOAL_PLANNING),

    # Money-in-hand with goal mention (allocation primary) → asset_allocation
    IntentCase("money-in-hand-with-goal-lump-sum",
               "I have 10 lakh to invest for my retirement in 20 years — where should I put it?",
               Intent.ASSET_ALLOCATION),
    IntentCase("money-in-hand-with-goal-monthly",
               "I can do 50k a month for my daughter's college in 12 years — how should I invest it?",
               Intent.ASSET_ALLOCATION),
    IntentCase("portfolio-with-goal-mention",
               "Should I add midcap to my portfolio for my retirement goal?",
               Intent.ASSET_ALLOCATION),

    # Where-to-invest with no goal → asset_allocation
    IntentCase("where-to-invest-no-goal",
               "I have 5 lakh to invest — where should I put it?",
               Intent.ASSET_ALLOCATION),
    IntentCase("fund-switch",
               "Should I switch from Axis Bluechip to Mirae Asset Large Cap?",
               Intent.ASSET_ALLOCATION),

    # Combined feasibility + allocation → goal_planning (tie-breaker)
    IntentCase("combined-feasibility-and-allocation",
               "At 50k a month, can I hit 10cr in 15 years, and where should I invest it?",
               Intent.GOAL_PLANNING),
    IntentCase("combined-trajectory-and-mix",
               "Will my 30k SIP get me to 3 crore in 18 years, and what mix should I use?",
               Intent.GOAL_PLANNING),

    # Adversarial: ordering bias — allocation phrasing first, goal at the end
    IntentCase("ordering-allocation-first",
               "Where should I invest my 50k monthly to retire with 5 crore in 15 years?",
               Intent.ASSET_ALLOCATION),

    # Adversarial: feasibility phrased as a question about achievability
    IntentCase("adversarial-realistic-target",
               "Is 1 crore in 10 years a realistic target for me?",
               Intent.GOAL_PLANNING),
]


@unittest.skipUnless(
    os.getenv("ANTHROPIC_API_KEY"),
    "ANTHROPIC_API_KEY not set — skipping live classifier boundary evals.",
)
class GoalPlanningBoundaryEvals(unittest.TestCase):
    """Live evals; require Anthropic credentials."""

    @classmethod
    def setUpClass(cls):
        cls.classifier = IntentClassifier()

    def _run(self, case: IntentCase) -> ClassificationResult:
        return self.classifier.classify(
            ClassificationInput(customer_question=case.question)
        )

    def _grade(self, case: IntentCase, result: ClassificationResult) -> tuple[bool, str]:
        ok = result.intent == case.expected
        return ok, "" if ok else f"expected={case.expected.value} got={result.intent.value}"

    def test_threshold(self):
        report = run_suite(
            suite="intent_boundary",
            cases=CASES,
            runner=self._run,
            grader=self._grade,
            threshold=12,    # 12 / 14 ≈ 86%
        )
        report.assert_threshold(self)


if __name__ == "__main__":
    unittest.main()
