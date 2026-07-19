"""In-memory lookup tables used by the rebalancing engine."""

from __future__ import annotations

from .config import (
    ARBITRAGE_FUND_CAP_PCT,
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
#   - `arbitrage` / `arbitrage_plus_income` 30%: low-volatility wrappers with
#     a small high-quality fund universe; same concentration-risk rationale.
SUBGROUP_FUND_CAP_PCT: dict[str, float] = {
    "multi_asset": MULTI_FUND_CAP_PCT,
    "short_debt": SHORT_DEBT_FUND_CAP_PCT,
    "arbitrage": ARBITRAGE_FUND_CAP_PCT,
    "arbitrage_plus_income": ARBITRAGE_FUND_CAP_PCT,
}


def cap_pct_for(asset_subgroup: str) -> float:
    """Per-fund cap (% of corpus) for `asset_subgroup`, with default fallback."""
    return SUBGROUP_FUND_CAP_PCT.get(asset_subgroup, OTHERS_FUND_CAP_PCT)


# Debt subgroups treated as one economic sleeve by step2b. Product decision
# (2026-07-18): all debt funds are assumed to deliver similar returns, so the
# tax-wrapper choice is worth making once at purchase and never revisited with
# money already deployed. Lives here rather than in the step so presentation can
# read it without importing a pipeline step.
DEBT_NETTING_POOL: frozenset[str] = frozenset(
    {
        "short_debt",
        "arbitrage",
        "arbitrage_plus_income",
    }
)
