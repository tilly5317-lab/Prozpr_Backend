"""In-memory lookup tables used by the rebalancing engine."""

from __future__ import annotations


# Sub-categories that get the multi-cap fund cap (default 20%) instead of the
# generic per-fund cap (default 10%). Source: workbook "Allocation 2" row 280.
MULTI_CAP_SUB_CATEGORIES: frozenset[str] = frozenset({
    "Multi Cap Fund",
    "Multi Asset Allocation",
})
