"""Live boundary evals: goal_planning vs asset_allocation.

These tests call the real Claude Haiku classifier and are skipped when
ANTHROPIC_API_KEY is not present in the environment. They exist to lock the
prompt's intent boundary defined in the design spec
docs/superpowers/specs/2026-05-01-goal-planning-routing-design.md §3.

Run manually:
    ANTHROPIC_API_KEY=sk-... python3 -m pytest \
        AI_Agents/tests/test_intent_classifier_boundary_evals.py -v
"""

from __future__ import annotations

import os
import sys
import unittest

# Make AI_Agents/src importable when running from the repo root.
_HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(_HERE, "..", "src"))

from intent_classifier import (  # noqa: E402
    ClassificationInput,
    Intent,
    IntentClassifier,
)


# (question, expected intent, label) — 14 cases covering the spec's boundary categories.
BOUNDARY_CASES: list[tuple[str, Intent, str]] = [
    # Pure feasibility (no money hook) → goal_planning
    ("I want to retire in 15 years with 5 crore — is that possible?",
     Intent.GOAL_PLANNING, "feasibility-only-retirement"),
    ("Will my current SIP be enough to hit 2 crore by 2040?",
     Intent.GOAL_PLANNING, "feasibility-only-trajectory"),
    ("Can I afford a 1cr house down-payment in 7 years?",
     Intent.GOAL_PLANNING, "feasibility-only-house"),

    # Required savings → goal_planning
    ("How much should I save each month for my daughter's college in 10 years?",
     Intent.GOAL_PLANNING, "required-savings-college"),
    ("How much do I need to invest monthly to retire with 5 crore in 20 years?",
     Intent.GOAL_PLANNING, "required-savings-retirement"),

    # Money-in-hand with goal mention (allocation primary) → asset_allocation
    ("I have 10 lakh to invest for my retirement in 20 years — where should I put it?",
     Intent.ASSET_ALLOCATION, "money-in-hand-with-goal-lump-sum"),
    ("I can do 50k a month for my daughter's college in 12 years — how should I invest it?",
     Intent.ASSET_ALLOCATION, "money-in-hand-with-goal-monthly"),
    ("Should I add midcap to my portfolio for my retirement goal?",
     Intent.ASSET_ALLOCATION, "portfolio-with-goal-mention"),

    # Where-to-invest with no goal → asset_allocation
    ("I have 5 lakh to invest — where should I put it?",
     Intent.ASSET_ALLOCATION, "where-to-invest-no-goal"),
    ("Should I switch from Axis Bluechip to Mirae Asset Large Cap?",
     Intent.ASSET_ALLOCATION, "fund-switch"),

    # Combined feasibility + allocation → goal_planning (tie-breaker)
    ("At 50k a month, can I hit 10cr in 15 years, and where should I invest it?",
     Intent.GOAL_PLANNING, "combined-feasibility-and-allocation"),
    ("Will my 30k SIP get me to 3 crore in 18 years, and what mix should I use?",
     Intent.GOAL_PLANNING, "combined-trajectory-and-mix"),

    # Adversarial: ordering bias — allocation phrasing first, goal at the end
    ("Where should I invest my 50k monthly to retire with 5 crore in 15 years?",
     Intent.ASSET_ALLOCATION, "ordering-allocation-first"),

    # Adversarial: feasibility phrased as a question about achievability with no money mention
    ("Is 1 crore in 10 years a realistic target for me?",
     Intent.GOAL_PLANNING, "adversarial-realistic-target"),
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

    def test_boundary_cases_meet_threshold(self):
        passes: list[str] = []
        failures: list[tuple[str, str, str, str]] = []  # (label, question, expected, actual)

        for question, expected, label in BOUNDARY_CASES:
            result = self.classifier.classify(
                ClassificationInput(customer_question=question)
            )
            if result.intent == expected:
                passes.append(label)
            else:
                failures.append((label, question, expected.value, result.intent.value))

        total = len(BOUNDARY_CASES)
        threshold = 12  # 12 / 14 ≈ 86%
        msg_lines = [f"Boundary eval: {len(passes)} / {total} passed."]
        for label, q, exp, got in failures:
            msg_lines.append(f"  - [{label}] expected={exp} got={got} :: {q!r}")
        msg = "\n".join(msg_lines)

        # Always print the result line so manual runs surface the score.
        print("\n" + msg)

        self.assertGreaterEqual(
            len(passes), threshold,
            f"Boundary eval below threshold ({len(passes)}/{total} < {threshold}).\n{msg}",
        )


if __name__ == "__main__":
    unittest.main()
