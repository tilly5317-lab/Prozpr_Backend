"""Trailing-return primitives — pure functions, no I/O.

``cagr`` annualises a point-to-point growth (start NAV → end NAV over N years)
into a compound annual growth rate. Used for fund track-record returns.
"""

from typing import Optional


def cagr(start_value: float, end_value: float, years: float) -> Optional[float]:
    """Compound annual growth rate as a percentage.

    ``(end/start) ** (1/years) - 1``, times 100. Returns ``None`` when the inputs
    can't yield a real rate (non-positive start value or non-positive years).
    """
    if start_value <= 0 or years <= 0:
        return None
    return round(((end_value / start_value) ** (1.0 / years) - 1.0) * 100.0, 2)
