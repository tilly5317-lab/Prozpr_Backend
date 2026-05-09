"""Inflation primitives."""
from __future__ import annotations


def inflate(amount_pv: float, rate: float, years: float) -> float:
    """Inflate amount from PV to FV at given rate."""
    if years < 0:
        raise ValueError(f"years must be >= 0, got {years}")
    return amount_pv * (1 + rate) ** years


def real_rate(nominal: float, inflation: float) -> float:
    """Fisher equation: real_rate = (1 + nominal) / (1 + inflation) - 1."""
    return (1 + nominal) / (1 + inflation) - 1
