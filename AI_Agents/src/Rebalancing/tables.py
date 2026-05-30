"""In-memory lookup tables used by the rebalancing engine."""

from __future__ import annotations

from .config import (
    MULTI_FUND_CAP_PCT,
    OTHERS_FUND_CAP_PCT,
    SHORT_DEBT_FUND_CAP_PCT,
)


# Per-fund concentration cap (% of corpus) keyed by `asset_subgroup`.
# Missing keys fall back to `OTHERS_FUND_CAP_PCT` via `cap_pct_for(...)`.
# Sources:
#   - `multi_asset` 20%: multi-asset funds are internally diversified across
#     asset classes; per-fund concentration risk is lower than single-class.
#   - `short_debt` 30%: Excel R247 — short-duration debt fund universe is
#     small and high-quality; concentration risk is correspondingly lower.
SUBGROUP_FUND_CAP_PCT: dict[str, float] = {
    "multi_asset": MULTI_FUND_CAP_PCT,
    "short_debt": SHORT_DEBT_FUND_CAP_PCT,
}


def cap_pct_for(asset_subgroup: str) -> float:
    """Per-fund cap (% of corpus) for `asset_subgroup`, with default fallback."""
    return SUBGROUP_FUND_CAP_PCT.get(asset_subgroup, OTHERS_FUND_CAP_PCT)
