"""Date helpers for Indian Financial Year math."""
from __future__ import annotations
from datetime import date
from calendar import monthrange


def fy_for_date(d: date) -> int:
    """Return the Indian FY year for a given date.
    Indian FY runs Apr 1 → Mar 31. The FY year is the year of the closing March.
    Mar 31 2026 → 2026; Apr 1 2026 → 2027.
    """
    return d.year if d.month <= 3 else d.year + 1


def fy_end_after(d: date) -> date:
    """Return the FY-end (March 31) on or after the given date."""
    fy = fy_for_date(d)
    return date(fy, 3, 31)


def eomonth(d: date, months_offset: int = 0) -> date:
    """End-of-month date, offset by `months_offset` months. Excel's EOMONTH equivalent."""
    total_months = d.month - 1 + months_offset
    year = d.year + total_months // 12
    month = total_months % 12 + 1
    last_day = monthrange(year, month)[1]
    return date(year, month, last_day)


def year_fraction(start: date, end: date) -> float:
    """Year fraction between two dates (calendar days / 365.25)."""
    return (end - start).days / 365.25
