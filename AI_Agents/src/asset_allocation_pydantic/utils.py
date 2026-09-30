from __future__ import annotations

from math import ceil


def round_to_rupee(x: float) -> int:
    """Round to the nearest whole rupee using round-half-up. Negative or zero inputs return 0.

    The engines stay at rupee precision; ₹100 rounding belongs to the final
    trade / SIP / lump-sum amounts (Rebalancing `rounding_step`,
    additional_investment `rounding_multiple_inr`)."""
    if x <= 0:
        return 0
    return int(x + 0.5)


def ceil_to_rupee(x: float) -> int:
    """Round up to the next whole rupee. Negative or zero inputs return 0."""
    if x <= 0:
        return 0
    return int(ceil(x))


def ceil_to_half(score: float) -> float:
    """Round up to nearest 0.5; clamp to [1.0, 10.0]."""
    score = max(1.0, min(10.0, float(score)))
    return min(10.0, ceil(score * 2) / 2)


def proportional_scale(values: list[float], target_sum: float) -> list[float]:
    """Scale values so their sum equals target_sum. Returns zeros if input sum is 0."""
    s = sum(values)
    if s <= 0:
        return [0.0 for _ in values]
    return [v * target_sum / s for v in values]
