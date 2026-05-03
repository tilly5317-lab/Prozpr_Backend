"""Shared list pagination limits."""

from __future__ import annotations

DEFAULT_LIMIT = 50
MAX_LIMIT = 200


def clamp_skip_limit(skip: int, limit: int) -> tuple[int, int]:
    s = max(skip, 0)
    lim = min(max(limit, 1), MAX_LIMIT)
    return s, lim
