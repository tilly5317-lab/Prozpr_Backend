"""Extended internal rate of return (XIRR) for irregular cashflows.

Pure Python, no I/O, no LLM. Hand-rolled Newton-Raphson with a bisection
fallback for cases where Newton diverges, oscillates, or hits a derivative
of zero.
"""

from __future__ import annotations

from datetime import date
from typing import Sequence

_DAYS_PER_YEAR = 365.0
_MAX_NEWTON_ITERS = 100
_TOL = 1e-7
_MAX_BISECT_ITERS = 200
_RATE_MIN = -0.999999  # (1+rate) must stay > 0
_RATE_MAX = 1e6


def _npv(rate: float, years: list[float], amounts: list[float]) -> float:
    base = 1.0 + rate
    return sum(a / base**y for y, a in zip(years, amounts))


def _dnpv(rate: float, years: list[float], amounts: list[float]) -> float:
    base = 1.0 + rate
    return sum(-y * a / base ** (y + 1.0) for y, a in zip(years, amounts))


def xirr(cashflows: Sequence[tuple[date, float]], guess: float = 0.1) -> float | None:
    """Annualised IRR for cashflows on arbitrary dates.

    Args:
        cashflows: ``(date, signed amount)`` pairs. Outflows negative, inflows positive.
        guess: Newton-Raphson seed (annualised, e.g. 0.1 for 10%).

    Returns:
        Annualised rate as a decimal (``0.12`` == 12%), or ``None`` when:
            - fewer than 2 cashflows,
            - all cashflows have the same sign (no real root),
            - neither Newton nor the bisection fallback converges.
    """
    if len(cashflows) < 2:
        return None
    has_pos = any(cf > 0 for _, cf in cashflows)
    has_neg = any(cf < 0 for _, cf in cashflows)
    if not (has_pos and has_neg):
        return None

    d0 = min(d for d, _ in cashflows)
    years = [(d - d0).days / _DAYS_PER_YEAR for d, _ in cashflows]
    amounts = [float(cf) for _, cf in cashflows]

    rate = guess
    for _ in range(_MAX_NEWTON_ITERS):
        if rate <= _RATE_MIN or rate >= _RATE_MAX:
            break
        f = _npv(rate, years, amounts)
        if abs(f) < _TOL:
            return rate
        df = _dnpv(rate, years, amounts)
        if df == 0.0:
            break
        step = f / df
        new_rate = rate - step
        if new_rate != new_rate:  # NaN
            break
        if abs(step) < _TOL:
            return new_rate
        rate = new_rate

    return _bisect(years, amounts)


def _bisect(years: list[float], amounts: list[float]) -> float | None:
    """Scan a grid of rates to bracket a sign change in NPV, then bisect."""
    grid = [
        -0.99,
        -0.9,
        -0.7,
        -0.5,
        -0.3,
        -0.1,
        -0.05,
        -0.01,
        0.0,
        0.01,
        0.05,
        0.1,
        0.2,
        0.5,
        1.0,
        2.0,
        5.0,
        10.0,
        100.0,
        1000.0,
    ]
    prev_rate = grid[0]
    prev_v = _npv(prev_rate, years, amounts)
    lo = hi = None
    for r in grid[1:]:
        v = _npv(r, years, amounts)
        if prev_v == 0.0:
            return prev_rate
        if v == 0.0:
            return r
        if (prev_v < 0) != (v < 0):
            lo, hi = (prev_rate, r) if prev_v > 0 else (r, prev_rate)
            break
        prev_rate, prev_v = r, v

    if lo is None:
        return None

    # Invariant: NPV(lo) > 0, NPV(hi) < 0 (NPV is monotone-decreasing for typical XIRR shapes).
    for _ in range(_MAX_BISECT_ITERS):
        mid = 0.5 * (lo + hi)
        v = _npv(mid, years, amounts)
        if abs(v) < _TOL or abs(hi - lo) < _TOL:
            return mid
        if v > 0:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)
