"""Engine date helpers + ROUND_THOUSAND convention helper.

Re-exports from financial_primitives.dates where applicable.
"""
from __future__ import annotations
from datetime import date

from financial_primitives.dates import fy_for_date, fy_end_after, eomonth


def _round_thousand(x: float) -> float:
    """Round to nearest 1000, half-away-from-zero (matches Excel ROUND(_, -3))."""
    if x >= 0:
        return float(int((x + 500) // 1000) * 1000)
    else:
        return -float(int((-x + 500) // 1000) * 1000)


def _add_years(d: date, years: int) -> date:
    """Add whole years to a date; clamp Feb 29 to Feb 28 in non-leap years."""
    try:
        return d.replace(year=d.year + years)
    except ValueError:
        # Feb 29 in non-leap target year — clamp to Feb 28.
        return d.replace(year=d.year + years, day=28)


def near_term_cutoff(latest_update_date: date, years: int = 2) -> date:
    """Near-term cutoff = FY-end on/after (latest_update + N years)."""
    return fy_end_after(_add_years(latest_update_date, years))


def medium_term_cutoff(near_term_end: date, years: int = 3) -> date:
    """Medium-term cutoff = FY-end on/after (near_term_end + N years)."""
    return fy_end_after(_add_years(near_term_end, years))


def fy_years_between(d1: date, d2: date) -> int:
    """Integer Indian-FY year difference (d2 minus d1), clamped at zero.

    Used for inflation FV math (inflate at goal_date by
    `(1+rate)^fy_years_between(today, goal_date)`). NOT used for PV-discount of
    goal payouts to today — that is day-precise (EOMONTH(goal_date)/365) to
    match Excel's headline cells O113 / S105 exactly.

    Examples (FY runs Apr-Mar):
        fy_years_between(2026-05-09, 2026-12-15) == 0   # same FY (FY27)
        fy_years_between(2026-05-09, 2027-04-01) == 1   # next FY (FY28)
        fy_years_between(2026-05-09, 2031-03-31) == 4   # 4 FYs ahead (FY27 -> FY31)
    """
    return max(fy_for_date(d2) - fy_for_date(d1), 0)


__all__ = [
    "_round_thousand", "near_term_cutoff", "medium_term_cutoff",
    "fy_for_date", "fy_end_after", "eomonth", "fy_years_between",
]
