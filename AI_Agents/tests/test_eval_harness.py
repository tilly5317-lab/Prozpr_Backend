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
