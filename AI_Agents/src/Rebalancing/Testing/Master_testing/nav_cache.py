"""NAV cache for dev fixtures.

Loads `{isin: current_nav}` once from `mf_subgroup_mapped.csv` (the same
upstream MF reference used to build `Prozpr_fund_ranking.csv`). The dev
runner uses this to populate realistic per-fund NAVs on synthetic
`FundRowInput` rows.

Production reads NAV from the MF NAV cache table; this module is a
dev-only stand-in. When the real `rebalancing_input_builder.py` is
built, it'll source NAV from `MfNavLatest` instead.
"""

from __future__ import annotations

import csv
from decimal import Decimal
from functools import lru_cache
from pathlib import Path


# Testing/Master_testing/nav_cache.py → Prozpr_Backend/MF_Logics/...
# parents: [0]=Master_testing [1]=Testing [2]=Rebalancing [3]=src [4]=AI_Agents [5]=Prozpr_Backend
_CSV_PATH = (
    Path(__file__).resolve().parents[5]
    / "MF_Logics" / "Mututal_Funds_data_extraction" / "mf_subgroup_mapped.csv"
)

# Fallback when an ISIN isn't in the cache (e.g., synthetic BAD test fund).
DEFAULT_NAV = Decimal("100")


@lru_cache(maxsize=1)
def _load_nav_map() -> dict[str, Decimal]:
    """Build `{isin: nav}` from the MF CSV. Same ISIN may appear multiple
    times across snapshot dates; last write wins (the CSV is generally
    deduped to one snapshot but we don't depend on it)."""
    out: dict[str, Decimal] = {}
    if not _CSV_PATH.exists():
        return out
    with open(_CSV_PATH) as f:
        for row in csv.DictReader(f):
            isin = (row.get("isinGrowth") or "").strip()
            nav_str = (row.get("nav") or "").strip()
            if not isin or not nav_str:
                continue
            try:
                nav = Decimal(nav_str)
            except Exception:
                continue
            if nav <= 0:
                continue
            out[isin] = nav
    return out


def get_nav(isin: str) -> Decimal:
    """Return the cached NAV for an ISIN. Falls back to DEFAULT_NAV when
    the ISIN isn't in the cache (e.g., synthetic BAD test fund)."""
    return _load_nav_map().get(isin, DEFAULT_NAV)
