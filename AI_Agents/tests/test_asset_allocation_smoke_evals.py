"""Smoke evals for asset_allocation_pydantic.

Runs `run_allocation(...)` with a no-LLM rationale fallback, so executes on
every commit without ANTHROPIC_API_KEY. Threshold = len(CASES) — deterministic
checks, all must pass.

Spec: docs/superpowers/specs/2026-05-02-shared-eval-harness-design.md

CRITICAL: rationale_fn=None falls back to a live LLM call inside
step7_presentation. We pass the public no-op `no_llm_rationale_fn` to keep
the suite offline.
"""
from __future__ import annotations

import os
import sys
import unittest
from dataclasses import dataclass
from typing import Callable, Literal

# Make AI_Agents/src importable when running from the repo root.
_HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(_HERE, "..", "src"))

from asset_allocation_pydantic import (  # noqa: E402
    AllocationInput,
    Goal,
    GoalAllocationOutput,
    run_allocation,
)
from asset_allocation_pydantic.steps._rationale_llm import no_llm_rationale_fn  # noqa: E402
from _eval_harness import run_suite  # noqa: E402

RiskBand = Literal["conservative", "balanced", "aggressive"]


@dataclass(frozen=True)
class AllocCase:
    label: str
    inp: AllocationInput
    expected_risk_band: RiskBand


# ---------- Case builders ----------

def _conservative_60yo() -> AllocationInput:
    return AllocationInput(
        effective_risk_score=2.5,
        age=60,
        annual_income=2_400_000,
        osi=0.4,
        savings_rate_adjustment="none",
        gap_exceeds_3=False,
        total_corpus=10_000_000,
        monthly_household_expense=80_000,
        tax_regime="new",
        effective_tax_rate=20.0,
        goals=[
            Goal(
                goal_name="Retirement income",
                time_to_goal_months=24,
                amount_needed=5_000_000,
                goal_priority="non_negotiable",
            ),
        ],
    )


def _aggressive_30yo() -> AllocationInput:
    return AllocationInput(
        effective_risk_score=8.5,
        age=30,
        annual_income=2_000_000,
        osi=0.7,
        savings_rate_adjustment="none",
        gap_exceeds_3=False,
        total_corpus=2_000_000,
        monthly_household_expense=60_000,
        tax_regime="new",
        effective_tax_rate=30.0,
        goals=[
            Goal(
                goal_name="Long-term wealth",
                time_to_goal_months=240,    # 20 years
                amount_needed=30_000_000,
                goal_priority="negotiable",
            ),
        ],
    )


CASES: list[AllocCase] = [
    AllocCase("conservative-60yo", _conservative_60yo(), "conservative"),
    AllocCase("aggressive-30yo",   _aggressive_30yo(),   "aggressive"),
]


# ---------- Deterministic checks ----------
# The next three (_check_grand_total_..., _check_actual_sum_..., _check_planned_pct_...)
# are correctness invariants — DO NOT loosen tolerances to make a case pass.
# _make_check_risk_band is a coarse heuristic and may be tuned (see its docstring).

_PCT_TOLERANCE = 0.5    # percentage points
_AMOUNT_TOLERANCE = 1.0  # rupees


def _check_grand_total_matches_corpus(
    c: AllocCase, out: GoalAllocationOutput
) -> tuple[bool, str]:
    expected = float(c.inp.total_corpus)
    actual = float(out.grand_total)
    ok = abs(actual - expected) <= _AMOUNT_TOLERANCE
    return ok, "" if ok else f"grand_total={actual} expected≈{expected}"


def _check_actual_sum_matches_grand_total(
    c: AllocCase, out: GoalAllocationOutput
) -> tuple[bool, str]:
    ok = bool(out.asset_class_breakdown.actual_sum_matches_grand_total)
    return ok, "" if ok else "asset_class_breakdown.actual_sum_matches_grand_total is False"


def _check_planned_pct_sum_to_100(
    c: AllocCase, out: GoalAllocationOutput
) -> tuple[bool, str]:
    p = out.asset_class_breakdown.planned
    total = p.equity_total_pct + p.debt_total_pct + p.others_total_pct
    ok = abs(total - 100.0) <= _PCT_TOLERANCE
    return ok, "" if ok else f"planned pct sum={total:.2f} (≠100±{_PCT_TOLERANCE})"


def _make_check_risk_band(
    expected: RiskBand,
) -> Callable[[AllocCase, GoalAllocationOutput], tuple[bool, str]]:
    """Coarse band derived from planned equity %.

    conservative: equity_pct <= 50
    balanced:     50 < equity_pct <= 70
    aggressive:   equity_pct > 70

    Thresholds are deliberately coarse; tighten in a follow-up session once we
    have more cases to anchor expected values.
    """
    def _check(c: AllocCase, out: GoalAllocationOutput) -> tuple[bool, str]:
        eq = out.asset_class_breakdown.planned.equity_total_pct
        if expected == "conservative":
            ok = eq <= 50.0
        elif expected == "balanced":
            ok = 50.0 < eq <= 70.0
        else:    # aggressive
            ok = eq > 70.0
        return ok, "" if ok else f"expected={expected} got equity_pct={eq:.1f}"
    return _check


# ---------- TestCase ----------

class AssetAllocationSmokeEvals(unittest.TestCase):
    """Deterministic smoke eval — runs without an API key."""

    def _run(self, case: AllocCase) -> GoalAllocationOutput:
        return run_allocation(case.inp, rationale_fn=no_llm_rationale_fn)

    def _grade(
        self, case: AllocCase, out: GoalAllocationOutput
    ) -> tuple[bool, str]:
        checks = (
            _check_grand_total_matches_corpus,
            _check_actual_sum_matches_grand_total,
            _check_planned_pct_sum_to_100,
            _make_check_risk_band(case.expected_risk_band),
        )
        for check in checks:
            ok, detail = check(case, out)
            if not ok:
                return False, detail
        return True, ""

    def test_threshold(self):
        report = run_suite(
            suite="asset_allocation_smoke",
            cases=CASES,
            runner=self._run,
            grader=self._grade,
            threshold=len(CASES),    # all must pass — deterministic
        )
        report.assert_threshold(self)


if __name__ == "__main__":
    unittest.main()
