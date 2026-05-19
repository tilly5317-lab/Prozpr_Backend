"""Inflation primitives."""
from __future__ import annotations


def inflate(amount_pv: float, rate: float, years: float) -> float:
    """Inflate `amount_pv` by `rate` over `years`.

    Formula: amount_pv * (1 + rate) ** years.

    `rate` is a fraction (e.g., 0.06 for 6%). `years` may be fractional
    (e.g., 2.74) — useful for day-precise inflation calculations such as
    `(target_date - today).days / 365`.
    """
    if years < 0:
        raise ValueError(f"years must be >= 0, got {years}")
    return amount_pv * (1 + rate) ** years


def real_rate(nominal: float, inflation: float) -> float:
    """Fisher equation: real_rate = (1 + nominal) / (1 + inflation) - 1."""
    return (1 + nominal) / (1 + inflation) - 1
