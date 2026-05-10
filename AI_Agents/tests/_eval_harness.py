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
