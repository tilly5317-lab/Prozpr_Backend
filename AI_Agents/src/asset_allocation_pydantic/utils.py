from __future__ import annotations

from math import ceil


def round_to_100(x: float) -> int:
    """Round to nearest multiple of 100 using round-half-up. Negative or zero inputs return 0."""
    if x <= 0:
        return 0
    return int(x / 100.0 + 0.5) * 100


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
