# Shared Eval Harness for AI Agents — design

**Date:** 2026-05-02
**Status:** Design
**Owner:** Amoul
**Builds on:** the inline boundary-eval pattern in `AI_Agents/tests/test_intent_classifier_boundary_evals.py` (cases list + threshold gate + env-skip), generalized so other agents do not have to re-implement it.

## Summary

`intent_classifier` already has a working "boundary eval" — a list of `(question, expected_intent, label)` tuples, run against the live Haiku classifier, gated on `ANTHROPIC_API_KEY`, scored against a `12/14` threshold. The pattern is right; it is just inlined into one test file. None of the other six active agents (`asset_allocation_pydantic`, `Rebalancing`, `market_commentary`, `portfolio_query`, `risk_profiling`, `chart_selector`) have an equivalent.

This spec extracts the runner / reporter / threshold logic into a single shared module — `AI_Agents/tests/_eval_harness.py` — and validates the abstraction by (a) migrating `intent_classifier`'s boundary evals onto it without behavior change, and (b) adding a minimal **smoke** eval suite for `asset_allocation_pydantic` that proves the harness works for a multi-step pipeline, not just a single LLM call.

The harness stays small on purpose: deterministic graders only, function-based API, one file. LLM-as-judge graders, run-history snapshots, and broader agent coverage are deliberate non-goals for this session.

## Goals

- One shared module that holds the threshold + reporting logic so each new agent's eval suite is ~10 lines of orchestration on top of its own cases/runner/grader.
- Behavior-preserving migration of `test_intent_classifier_boundary_evals.py` — same 14 cases, same threshold of 12, same env-gating, same printed summary on manual runs.
- A new `test_asset_allocation_smoke_evals.py` with 2 cases that runs the deterministic part of `run_allocation` (no `rationale_fn`) so it executes on every commit without an API key.
- Self-tests for the harness itself (no agent imports, no LLM) so the harness is correct independent of any caller.
- Smallest possible delta from the current pattern: still `unittest.TestCase`, still `@unittest.skipUnless` for live-LLM suites, still threshold-based pass/fail.

## Non-goals (follow-ups, not in this spec)

- LLM-as-judge / rubric graders. Needed for `market_commentary` and chat output, not in scope here. Confirmed in the brainstorm.
- Run-history JSON snapshots and run-to-run comparison. Useful when upgrading models (Sonnet 4.6 → 4.7); separate session.
- Eval suites for the other five agents (`Rebalancing`, `market_commentary`, `portfolio_query`, `risk_profiling`, `chart_selector`). Add one at a time, after the harness shape is proven on intent + allocation.
- Pytest parametrize migration (per-case CI granularity). Considered and rejected for now — threshold gating is the right answer for non-deterministic LLM outputs, and the `unittest` style matches existing tests.
- A grader library (reusable `schema_check`, `weights_sum_to_one`, etc.). Each agent owns its own checks initially. Extract only when a 3rd agent needs the same one.
- Cases-as-YAML / JSON fixtures. Hand-built typed `Case` dataclasses for now; revisit if a suite grows past ~10 cases.

## Locked design decisions

| Decision | Choice | Why |
|---|---|---|
| Scope of this session | Harness + intent migration + minimal asset_allocation smoke eval | Validates the abstraction against both single-LLM-call and multi-step-pipeline shapes without taking on full agent coverage |
| Grader graduation | Deterministic graders only (exact match, schema, numerical, assertions) | YAGNI — judge graders are a separate axis of complexity, not needed by intent or allocation |
| Module layout | Single file `AI_Agents/tests/_eval_harness.py`, leading underscore so pytest does not auto-discover it as a test file | Promote to a package only if it grows past one file |
| API shape | Function-based: `run_suite(...)` returns an `EvalReport`; each agent owns its own typed `Case` dataclass, `runner` callable, and `grader` callable | Smallest delta from current code; runner-as-callable fits both `IntentClassifier.classify(...)` and `run_allocation(...)`; graders compose as plain functions |
| Grader return shape | `tuple[bool, str]` — passed flag + short detail string | Minimal; trivially upgradable to a richer `GradeResult` later if judge graders need scores |
| Runner/grader exception handling | Caught inside `run_suite`, recorded as `passed=False, detail="<ExceptionType>: ..."`; suite continues | One bad case must not abort the suite; eval suites are gentler about errors than mocked unit tests |
| Reporter behavior | `EvalReport.assert_threshold(testcase)` always prints the suite summary before asserting, mirroring `test_intent_classifier_boundary_evals.py:108` | Manual runs surface the score even on success |
| Threshold semantics | Caller-supplied integer; `passes >= threshold` ⇒ suite passes | Non-deterministic suites set < total (e.g. 12/14); deterministic suites set `len(cases)` (all must pass) |
| Env-gating | At the unittest layer (`@unittest.skipUnless` on the TestCase), not inside the harness | Deterministic suites (asset_allocation smoke) need to run without an API key; live-LLM suites (intent boundary) keep their current skip |
| Asset_allocation runner | Calls `run_allocation(inp, rationale_fn=None)` | Steps 1–6 of the pipeline are deterministic computational logic; only `step7_presentation`'s optional `rationale_fn` calls an LLM. Skipping it ⇒ no flakiness, no API key, runs on every commit |
| Asset_allocation case count | 2 cases (one conservative-low-age, one aggressive-young) | Enough to exercise the multi-step pipeline shape through the harness; not trying to cover the matrix yet |
| Asset_allocation graders | Hand-written deterministic checks owned by the test file (e.g. `_check_grand_total_matches_corpus`, `_check_no_negative_amounts`, `_check_asset_class_pct_sum_to_100`, `_check_risk_band(expected)`) | Each agent owns its own checks until a 3rd consumer needs the same one — no premature library |
| `AllocationInput` construction | Hand-built in the test file, not loaded from a JSON fixture | At 2 cases, hand-built is more visible in diffs and fails loudly when the schema evolves; fixtures pay off at 5+ |

## Architecture

### Module layout

One file:

```
AI_Agents/tests/_eval_harness.py        # the shared module (this spec)
AI_Agents/tests/test_eval_harness.py    # self-tests for the harness, no agent imports
AI_Agents/tests/test_intent_classifier_boundary_evals.py    # migrated onto harness
AI_Agents/tests/test_asset_allocation_smoke_evals.py        # new, uses harness
```

Leading-underscore filename signals "shared helper, not a pytest-discovered test module". No `_eval/` subpackage. Promote later only if the file grows past one responsibility.

### API surface

```python
# AI_Agents/tests/_eval_harness.py

@dataclass(frozen=True)
class CaseResult:
    label: str
    passed: bool
    detail: str = ""              # short reason on failure (e.g. "expected=X got=Y")

@dataclass
class EvalReport:
    suite: str
    results: list[CaseResult]
    threshold: int

    @property
    def passes(self) -> int: ...
    @property
    def total(self) -> int: ...
    def summary(self) -> str: ...
        # Multi-line:
        #   "Suite <name>: P/T passed (threshold T_min)."
        #   "  - [label] detail"  for each failure
    def assert_threshold(self, tc: unittest.TestCase) -> None:
        # Always prints summary(); then asserts passes >= threshold.

def run_suite(
    *,
    suite: str,
    cases: Sequence[CaseT],
    runner: Callable[[CaseT], ResultT],
    grader: Callable[[CaseT, ResultT], tuple[bool, str]],
    threshold: int,
) -> EvalReport: ...
```

`CaseT` and `ResultT` are generic; each agent's test file binds them to its own `Case` dataclass and result type.

### Data flow

```
cases (Sequence[CaseT])
  ├── for each case:
  │     try:    result = runner(case)         ──── if exception → CaseResult(label, False, "RuntimeError: ...")
  │     try:    ok, detail = grader(case, result)  ── if exception → CaseResult(label, False, "RuntimeError: ...")
  │     else:   CaseResult(label, ok, detail or "")
  └── EvalReport(suite, results, threshold)

EvalReport.assert_threshold(tc):
  print(report.summary())            # always
  tc.assertGreaterEqual(report.passes, report.threshold, report.summary())
```

### `intent_classifier` migration

`test_intent_classifier_boundary_evals.py` keeps its 14 cases, threshold 12, and env-skip. The inline `for case in BOUNDARY_CASES: ...` block (current lines 87–113) is replaced by `run_suite(...)` plus a typed `IntentCase`:

```python
@dataclass(frozen=True)
class IntentCase:
    label: str
    question: str
    expected: Intent

CASES: list[IntentCase] = [
    IntentCase("feasibility-only-retirement",
               "I want to retire in 15 years with 5 crore — is that possible?",
               Intent.GOAL_PLANNING),
    # ... 13 more, content unchanged
]

def _run(case: IntentCase) -> ClassificationOutput:
    return _classifier.classify(ClassificationInput(customer_question=case.question))

def _grade(case: IntentCase, result: ClassificationOutput) -> tuple[bool, str]:
    ok = result.intent == case.expected
    return ok, "" if ok else f"expected={case.expected.value} got={result.intent.value}"

@unittest.skipUnless(os.getenv("ANTHROPIC_API_KEY"), "ANTHROPIC_API_KEY not set ...")
class GoalPlanningBoundaryEvals(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls._classifier = IntentClassifier()

    def test_threshold(self):
        report = run_suite(suite="intent_boundary", cases=CASES,
                           runner=_run, grader=_grade, threshold=12)
        report.assert_threshold(self)
```

Net change: an ~80-line test method becomes ~10 lines. No behavior change.

### `asset_allocation_pydantic` smoke eval

```python
# AI_Agents/tests/test_asset_allocation_smoke_evals.py

@dataclass(frozen=True)
class AllocCase:
    label: str
    inp: AllocationInput
    expected_risk_band: Literal["conservative", "balanced", "aggressive"]

CASES: list[AllocCase] = [
    AllocCase("conservative-60yo", AllocationInput(... risk_score=2 ...), "conservative"),
    AllocCase("aggressive-30yo",   AllocationInput(... risk_score=9 ...), "aggressive"),
]

def _run(c: AllocCase) -> GoalAllocationOutput:
    return run_allocation(c.inp, rationale_fn=None)   # no LLM call

def _grade(c: AllocCase, out: GoalAllocationOutput) -> tuple[bool, str]:
    for check in (_check_grand_total_matches_corpus,
                  _check_no_negative_amounts,
                  _check_asset_class_pct_sum_to_100,
                  _check_risk_band(c.expected_risk_band)):
        ok, detail = check(c, out)
        if not ok:
            return False, detail
    return True, ""

class AssetAllocationSmokeEvals(unittest.TestCase):
    def test_threshold(self):
        report = run_suite(suite="asset_allocation_smoke",
                           cases=CASES, runner=_run, grader=_grade,
                           threshold=len(CASES))      # all must pass — deterministic
        report.assert_threshold(self)
```

No `@unittest.skipUnless` — runs every commit.

### Harness self-tests

`AI_Agents/tests/test_eval_harness.py` covers, with stub runner/grader closures (no agent imports):

1. All cases pass → `assert_threshold` does not raise; `passes == total`.
2. Below-threshold suite → `assert_threshold` raises `AssertionError`; message contains the per-failure `detail`.
3. At-threshold boundary (e.g. 12/14, threshold=12) → does not raise.
4. Runner raises → that case is `passed=False, detail="<ExceptionType>: ..."`; subsequent cases still run.
5. Grader raises → same handling as runner.
6. Empty cases list → `total == 0`; `assert_threshold` with `threshold=0` does not raise.
7. `summary()` format → contains suite name, `P/T passed`, and one line per failed case with its `detail`.

These run every commit, no API key, no agent imports.

## Verification — what counts as "done"

- `python -m pytest AI_Agents/tests/test_eval_harness.py` — all green.
- `python -m pytest AI_Agents/tests/test_asset_allocation_smoke_evals.py` — all green (no `ANTHROPIC_API_KEY`).
- `python -m pytest AI_Agents/tests/test_intent_classifier.py` — unchanged, all green (sanity check on the mocked unit tests; the migration touches only the boundary-evals file).
- `ANTHROPIC_API_KEY=... python -m pytest AI_Agents/tests/test_intent_classifier_boundary_evals.py` — passes ≥ 12/14, same threshold as today.
- Pyright clean on the four touched/added files.
