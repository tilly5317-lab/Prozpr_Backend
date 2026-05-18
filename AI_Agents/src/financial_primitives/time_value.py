"""Time-value-of-money primitives. Pure Python, no LLM, no I/O."""
from __future__ import annotations


def future_value(pv: float, rate: float, years: float) -> float:
    """FV = PV × (1 + rate)^years. Annual compounding."""
    if years < 0:
        raise ValueError(f"years must be >= 0, got {years}")
    return pv * (1 + rate) ** years


def present_value(fv: float, rate: float, years: float) -> float:
    """PV = FV / (1 + rate)^years."""
    if years < 0:
        raise ValueError(f"years must be >= 0, got {years}")
    return fv / (1 + rate) ** years


def compound(principal: float, monthly_rate: float, months: int) -> float:
    """Compound at monthly rate for N months."""
    if months < 0:
        raise ValueError(f"months must be >= 0, got {months}")
    return principal * (1 + monthly_rate) ** months
