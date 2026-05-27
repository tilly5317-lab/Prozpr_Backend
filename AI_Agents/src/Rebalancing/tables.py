"""In-memory lookup tables used by the rebalancing engine."""

from __future__ import annotations


# Asset subgroups that get the multi-fund per-fund cap (default 20%) instead
# of the generic per-fund cap (default 10%). Multi-asset funds are
# internally diversified across asset classes, so per-fund concentration
# risk is lower and the cap is raised.
MULTI_FUND_CAP_SUBGROUPS: frozenset[str] = frozenset({
    "multi_asset",
})
