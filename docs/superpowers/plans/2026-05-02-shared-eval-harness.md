# Shared Eval Harness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract the inline boundary-eval pattern from `test_intent_classifier_boundary_evals.py` into a shared `AI_Agents/tests/_eval_harness.py`, migrate the intent classifier evals onto it, and add a deterministic smoke-eval suite for `asset_allocation_pydantic` that runs without an API key.

**Architecture:** Function-based harness (`run_suite(...)` → `EvalReport`); each agent test file owns its own typed `Case` dataclass, runner method, and grader method bound to a `unittest.TestCase`. Threshold gating preserved. Runner/grader exceptions are caught per-case so one failure does not abort the suite.

**Tech Stack:** Python 3, `unittest`, `dataclasses`, `pydantic` (already used by agents), pyright for type checking, ruff for lint. No new dependencies.

**Project state caveat:** `ailax/` is not a git repository. Each task ends with a **manual checkpoint** (run command, confirm output) instead of `git commit`. To take a rollback snapshot before a task, run `cp -R AI_Agents/tests AI_Agents/tests.snap-<task-name>` and remove on success.

**Spec:** `Prozpr_Backend/docs/superpowers/specs/2026-05-02-shared-eval-harness-design.md`

---

### Task 1: Build the shared harness with self-tests (TDD)

**Files:**
- Create: `AI_Agents/tests/_eval_harness.py`
- Create: `AI_Agents/tests/test_eval_harness.py`

- [ ] **Step 1: Write the failing self-tests**

Create `AI_Agents/tests/test_eval_harness.py`:

```python
"""Self-tests for AI_Agents/tests/_eval_harness.py.

No agent imports, no LLM calls. Pure-Python verification of the harness
behavior (pass-counting, threshold gating, exception capture, summary text).
"""
from __future__ import annotations

import unittest
from dataclasses import dataclass

from _eval_harness import run_suite


@dataclass(frozen=True)
class _StubCase:
    label: str
    inp: int
    expected: int


def _runner_passthrough(c: _StubCase) -> int:
    return c.inp


def _grader_eq(c: _StubCase, r: int) -> tuple[bool, str]:
    ok = r == c.expected
    return ok, "" if ok else f"expected={c.expected} got={r}"


CASES_ALL_PASS = [_StubCase("a", 1, 1), _StubCase("b", 2, 2)]
CASES_ONE_FAIL = [_StubCase("a", 1, 1), _StubCase("b", 2, 99)]


class TestEvalHarness(unittest.TestCase):

    def test_all_pass(self):
        report = run_suite(suite="t", cases=CASES_ALL_PASS,
                           runner=_runner_passthrough, grader=_grader_eq, threshold=2)
        self.assertEqual(report.passes, 2)
        self.assertEqual(report.total, 2)
        report.assert_threshold(self)  # must not raise

    def test_below_threshold_raises(self):
        report = run_suite(suite="t", cases=CASES_ONE_FAIL,
                           runner=_runner_passthrough, grader=_grader_eq, threshold=2)
        self.assertEqual(report.passes, 1)

        # We need a separate TestCase so the assert doesn't pollute self.
        inner = unittest.TestCase()
        with self.assertRaises(AssertionError) as ctx:
            report.assert_threshold(inner)
        self.assertIn("[b]", str(ctx.exception))
        self.assertIn("expected=99 got=2", str(ctx.exception))

    def test_at_threshold_does_not_raise(self):
        # 1/2 passed, threshold=1 → boundary holds
        report = run_suite(suite="t", cases=CASES_ONE_FAIL,
                           runner=_runner_passthrough, grader=_grader_eq, threshold=1)
        report.assert_threshold(self)  # must not raise

    def test_runner_exception_caught(self):
        def boom(c: _StubCase) -> int:
            if c.label == "b":
                raise RuntimeError("boom")
            return c.inp

        cases = [_StubCase("a", 1, 1), _StubCase("b", 2, 2), _StubCase("c", 3, 3)]
        report = run_suite(suite="t", cases=cases,
                           runner=boom, grader=_grader_eq, threshold=2)
        self.assertEqual(report.passes, 2)  # a and c still ran
        b = next(r for r in report.results if r.label == "b")
        self.assertFalse(b.passed)
        self.assertIn("RuntimeError", b.detail)
        self.assertIn("boom", b.detail)

    def test_grader_exception_caught(self):
        def grader(c: _StubCase, r: int) -> tuple[bool, str]:
            if c.label == "b":
                raise ValueError("grader-broken")
            return r == c.expected, ""

        cases = [_StubCase("a", 1, 1), _StubCase("b", 2, 2)]
        report = run_suite(suite="t", cases=cases,
                           runner=_runner_passthrough, grader=grader, threshold=1)
        b = next(r for r in report.results if r.label == "b")
        self.assertFalse(b.passed)
        self.assertIn("ValueError", b.detail)
        self.assertIn("grader-broken", b.detail)

    def test_empty_cases(self):
        report = run_suite(suite="t", cases=[],
                           runner=_runner_passthrough, grader=_grader_eq, threshold=0)
        self.assertEqual(report.total, 0)
        self.assertEqual(report.passes, 0)
        report.assert_threshold(self)  # threshold 0 → no raise

    def test_summary_format(self):
        report = run_suite(suite="my_suite", cases=CASES_ONE_FAIL,
                           runner=_runner_passthrough, grader=_grader_eq, threshold=2)
        s = report.summary()
        self.assertIn("Suite my_suite:", s)
        self.assertIn("1/2 passed", s)
        self.assertIn("(threshold 2)", s)
        self.assertIn("- [b]", s)
        self.assertIn("expected=99 got=2", s)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:
```bash
cd /Users/Amoul/Documents/AILAX_AI_Financial_advisor/ailax/Prozpr_Backend
python -m pytest AI_Agents/tests/test_eval_harness.py -v
```

Expected: collection or import error — `ModuleNotFoundError: No module named '_eval_harness'`.

- [ ] **Step 3: Implement `_eval_harness.py`**

Create `AI_Agents/tests/_eval_harness.py`:

```python
"""Shared eval harness for AI_Agents test suites.

Each agent test file provides a typed `Case` dataclass, a `runner` callable
(case → result), and a `grader` callable (case, result → (passed, detail)).
The harness loops cases, captures pass/fail, catches runner/grader exceptions
per-case, and asserts against a caller-supplied threshold.

Designed for deterministic graders. LLM-as-judge / rubric graders are out of
scope (see design spec 2026-05-02-shared-eval-harness-design.md).
"""
from __future__ import annotations

import unittest
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import TypeVar

CaseT = TypeVar("CaseT")
ResultT = TypeVar("ResultT")


@dataclass(frozen=True)
class CaseResult:
    label: str
    passed: bool
    detail: str = ""


@dataclass
class EvalReport:
    suite: str
    results: list[CaseResult]
    threshold: int

    @property
    def passes(self) -> int:
        return sum(1 for r in self.results if r.passed)

    @property
    def total(self) -> int:
        return len(self.results)

    def summary(self) -> str:
        head = (
            f"Suite {self.suite}: {self.passes}/{self.total} passed "
            f"(threshold {self.threshold})."
        )
        lines = [head]
        for r in self.results:
            if not r.passed:
                detail = r.detail or "(no detail)"
                lines.append(f"  - [{r.label}] {detail}")
        return "\n".join(lines)

    def assert_threshold(self, tc: unittest.TestCase) -> None:
        msg = self.summary()
        print("\n" + msg)
        tc.assertGreaterEqual(self.passes, self.threshold, msg)


def run_suite(
    *,
    suite: str,
    cases: Sequence[CaseT],
    runner: Callable[[CaseT], ResultT],
    grader: Callable[[CaseT, ResultT], tuple[bool, str]],
    threshold: int,
) -> EvalReport:
    results: list[CaseResult] = []
    for case in cases:
        label = getattr(case, "label", "<no-label>")
        try:
            result = runner(case)
        except Exception as e:  # noqa: BLE001 — by design: capture per-case
            results.append(CaseResult(label, False, f"{type(e).__name__}: {e}"))
            continue
        try:
            ok, detail = grader(case, result)
        except Exception as e:  # noqa: BLE001
            results.append(CaseResult(label, False, f"{type(e).__name__}: {e}"))
            continue
        results.append(CaseResult(label, bool(ok), detail or ""))
    return EvalReport(suite=suite, results=results, threshold=threshold)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run:
```bash
python -m pytest AI_Agents/tests/test_eval_harness.py -v
```

Expected: `7 passed`.

- [ ] **Step 5: Type-check the new files**

Run:
```bash
pyright AI_Agents/tests/_eval_harness.py AI_Agents/tests/test_eval_harness.py
```

Expected: `0 errors, 0 warnings, 0 informations`.

- [ ] **Step 6: Checkpoint**

Confirm the two new files exist and are non-empty:
```bash
ls -la AI_Agents/tests/_eval_harness.py AI_Agents/tests/test_eval_harness.py
```

Optional rollback snapshot before Task 2: `cp -R AI_Agents/tests AI_Agents/tests.snap-task1-done`.

---

### Task 2: Migrate `intent_classifier` boundary evals onto the harness

**Files:**
- Modify: `AI_Agents/tests/test_intent_classifier_boundary_evals.py` (full rewrite — same 14 cases, same threshold, same env-skip)
- Untouched: `AI_Agents/tests/test_intent_classifier.py` (mocked unit tests; sanity-checked at end)

- [ ] **Step 1: Read the current file as a baseline**

Run:
```bash
wc -l AI_Agents/tests/test_intent_classifier_boundary_evals.py
```

Expected: ~117 lines. The 14 case tuples and threshold=12 are the contract — preserve both.

- [ ] **Step 2: Replace the file with the harness-based version**

Overwrite `AI_Agents/tests/test_intent_classifier_boundary_evals.py`:

```python
"""Live boundary evals: intent_classifier intent boundaries.

Migrated to use the shared eval harness (AI_Agents/tests/_eval_harness.py).
Behavior preserved: same 14 cases, same threshold of 12, same env-skip when
ANTHROPIC_API_KEY is missing.

Spec: docs/superpowers/specs/2026-05-02-shared-eval-harness-design.md

Run manually:
    ANTHROPIC_API_KEY=sk-... python -m pytest \
        AI_Agents/tests/test_intent_classifier_boundary_evals.py -v
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
    ClassificationOutput,
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

    def _run(self, case: IntentCase) -> ClassificationOutput:
        return self.classifier.classify(
            ClassificationInput(customer_question=case.question)
        )

    def _grade(self, case: IntentCase, result: ClassificationOutput) -> tuple[bool, str]:
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
```

- [ ] **Step 3: Verify the mocked unit tests are unaffected**

Run:
```bash
python -m pytest AI_Agents/tests/test_intent_classifier.py -v
```

Expected: all green (these tests were not touched; sanity check that imports still resolve in this dir).

- [ ] **Step 4: Verify the migrated boundary evals collect (no API key needed)**

Run without `ANTHROPIC_API_KEY`:
```bash
python -m pytest AI_Agents/tests/test_intent_classifier_boundary_evals.py -v
```

Expected: `1 skipped` with message "ANTHROPIC_API_KEY not set — skipping live classifier boundary evals." Confirms env-gating still works.

- [ ] **Step 5: Run the migrated boundary evals against the live classifier**

Run:
```bash
ANTHROPIC_API_KEY="$ANTHROPIC_API_KEY" python -m pytest \
    AI_Agents/tests/test_intent_classifier_boundary_evals.py -v -s
```

Expected: `1 passed`. The `-s` flag surfaces the printed summary, which should read:
```
Suite intent_boundary: N/14 passed (threshold 12).
```
with `N >= 12`. If `N < 12` and the failure pattern looks identical to before the migration, that is **not** a migration regression — it is a model/prompt drift unrelated to this task; record the failures and move on.

- [ ] **Step 6: Type-check the migrated file**

Run:
```bash
pyright AI_Agents/tests/test_intent_classifier_boundary_evals.py
```

Expected: `0 errors`.

- [ ] **Step 7: Checkpoint**

The migration succeeded if and only if all of: mocked tests still green, env-skip still works, live evals pass ≥ 12/14 (or fail with the same set of cases as before the migration).

Optional rollback snapshot before Task 3: `cp -R AI_Agents/tests AI_Agents/tests.snap-task2-done`.

---

### Task 3: Add `asset_allocation_pydantic` smoke eval

**Files:**
- Create: `AI_Agents/tests/test_asset_allocation_smoke_evals.py`

**Critical detail:** `step7_presentation.py:273` does `rationale_fn or _rationale_llm.generate_rationales` — if you pass `rationale_fn=None`, the pipeline falls back to a **live LLM call**. Pass an explicit no-LLM function (`_fallback_response`-based, copying the pattern from `AI_Agents/src/asset_allocation_pydantic/Testing/test_no_fund_mapping.py:18-19`).

- [ ] **Step 1: Write the smoke eval test file**

Create `AI_Agents/tests/test_asset_allocation_smoke_evals.py`:

```python
"""Smoke evals for asset_allocation_pydantic.

Runs `run_allocation(...)` with a no-LLM rationale fallback, so executes on
every commit without ANTHROPIC_API_KEY. Threshold = len(CASES) — deterministic
checks, all must pass.

Spec: docs/superpowers/specs/2026-05-02-shared-eval-harness-design.md

CRITICAL: rationale_fn=None falls back to a live LLM call inside
step7_presentation. We pass an explicit no-op (_fallback_response wrapper) to
keep the suite offline.
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
from asset_allocation_pydantic.steps._rationale_llm import _fallback_response  # noqa: E402
from _eval_harness import run_suite  # noqa: E402

RiskBand = Literal["conservative", "balanced", "aggressive"]


def _no_llm(_client_summary, bucket_allocations, _aggregated_subgroups):
    """Identical pattern to AI_Agents/src/.../Testing/test_no_fund_mapping.py."""
    return _fallback_response(bucket_allocations)


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
        return run_allocation(case.inp, rationale_fn=_no_llm)

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
```

- [ ] **Step 2: Run the smoke eval (no API key)**

Run:
```bash
unset ANTHROPIC_API_KEY
python -m pytest AI_Agents/tests/test_asset_allocation_smoke_evals.py -v -s
```

Expected: `1 passed`. Printed summary:
```
Suite asset_allocation_smoke: 2/2 passed (threshold 2).
```

- [ ] **Step 3: If a check fails, debug iteratively**

If the run prints e.g. `[conservative-60yo] expected=conservative got equity_pct=58.3`, the failure means our coarse risk-band thresholds disagree with the pipeline's actual output. Two options, in order:

1. Print the full output once to anchor: temporarily add `print(out.model_dump_json(indent=2))` in `_run` for one case, re-run with `-s`, inspect, then revert.
2. Tune either the case input (e.g. drop `effective_risk_score` to 1.5 for conservative) or the risk-band thresholds in `_make_check_risk_band` to match observed pipeline behavior. Document the chosen thresholds in the docstring.

Do NOT loosen `_check_grand_total_matches_corpus`, `_check_actual_sum_matches_grand_total`, or `_check_planned_pct_sum_to_100` — those are correctness invariants. Only tune `_make_check_risk_band` thresholds, which encode a coarse heuristic.

If the suite passes on first run, skip this step.

- [ ] **Step 4: Type-check the new file**

Run:
```bash
pyright AI_Agents/tests/test_asset_allocation_smoke_evals.py
```

Expected: `0 errors`.

- [ ] **Step 5: Lint the new file**

Run:
```bash
ruff check AI_Agents/tests/test_asset_allocation_smoke_evals.py
```

Expected: `All checks passed!`.

- [ ] **Step 6: Checkpoint**

```bash
ls -la AI_Agents/tests/test_asset_allocation_smoke_evals.py
python -m pytest AI_Agents/tests/test_asset_allocation_smoke_evals.py -v
```

Optional rollback snapshot before Task 4: `cp -R AI_Agents/tests AI_Agents/tests.snap-task3-done`.

---

### Task 4: Final cross-suite verification

**Files:** None modified — verification only.

- [ ] **Step 1: Harness self-tests pass (no API key)**

```bash
unset ANTHROPIC_API_KEY
python -m pytest AI_Agents/tests/test_eval_harness.py -v
```

Expected: `7 passed`.

- [ ] **Step 2: Asset allocation smoke evals pass (no API key)**

```bash
python -m pytest AI_Agents/tests/test_asset_allocation_smoke_evals.py -v
```

Expected: `1 passed`.

- [ ] **Step 3: Intent classifier mocked unit tests still pass (no API key)**

```bash
python -m pytest AI_Agents/tests/test_intent_classifier.py -v
```

Expected: all green; count unchanged from before this work.

- [ ] **Step 4: Intent classifier boundary evals skip (no API key)**

```bash
python -m pytest AI_Agents/tests/test_intent_classifier_boundary_evals.py -v
```

Expected: `1 skipped`.

- [ ] **Step 5: Intent classifier boundary evals pass (with API key)**

```bash
ANTHROPIC_API_KEY="$ANTHROPIC_API_KEY" python -m pytest \
    AI_Agents/tests/test_intent_classifier_boundary_evals.py -v -s
```

Expected: `1 passed`, with printed summary `Suite intent_boundary: N/14 passed (threshold 12).` where `N >= 12`.

- [ ] **Step 6: Pyright + ruff clean across all touched files**

```bash
pyright \
    AI_Agents/tests/_eval_harness.py \
    AI_Agents/tests/test_eval_harness.py \
    AI_Agents/tests/test_intent_classifier_boundary_evals.py \
    AI_Agents/tests/test_asset_allocation_smoke_evals.py
ruff check \
    AI_Agents/tests/_eval_harness.py \
    AI_Agents/tests/test_eval_harness.py \
    AI_Agents/tests/test_intent_classifier_boundary_evals.py \
    AI_Agents/tests/test_asset_allocation_smoke_evals.py
```

Expected: both clean — `0 errors`, `All checks passed!`.

- [ ] **Step 7: Drop the rollback snapshots**

If you took `cp -R` snapshots between tasks and everything is green, remove them:
```bash
rm -rf AI_Agents/tests.snap-task1-done \
       AI_Agents/tests.snap-task2-done \
       AI_Agents/tests.snap-task3-done
```

- [ ] **Step 8: Done.**

The harness is in place; intent_classifier evals run on it; asset_allocation has its first deterministic smoke suite. Next session: add boundary evals for `Rebalancing` (numerical + assertion graders) using the same harness shape.
