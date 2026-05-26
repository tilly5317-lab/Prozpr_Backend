"""Engine date helpers + ROUND_THOUSAND convention helper.

Re-exports from financial_primitives.dates where applicable.
"""
from __future__ import annotations
from datetime import date

from financial_primitives.dates import fy_for_date, fy_end_after, eomonth


def _round_thousand(x: float) -> float:
    """Round to nearest 1000, half-away-from-zero."""
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
    """Near-term cutoff = exact date (latest_update + N years)."""
    return _add_years(latest_update_date, years)


def medium_term_cutoff(near_term_end: date, years: int = 3) -> date:
    """Medium-term cutoff = exact date (near_term_end + N years)."""
    return _add_years(near_term_end, years)


__all__ = [
    "_round_thousand", "near_term_cutoff", "medium_term_cutoff",
    "fy_for_date", "fy_end_after", "eomonth",
]
