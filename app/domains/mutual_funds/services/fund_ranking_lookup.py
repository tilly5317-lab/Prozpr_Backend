"""Thin lookups over the cached fund-ranking CSV.

The loader (`rebal_engine.fund_rank`) exposes the shortlist grouped by
`asset_subgroup`; mutual_fund_query needs by-ISIN access and like-for-like peers by
`sub_category`, so those wrappers live here.
"""

from __future__ import annotations

from app.domains.rebalancing.services.rebal_engine.fund_rank import (
    FundRankRow,
    get_fund_ranking,
)


def _all_rows() -> list[FundRankRow]:
    return [r for rows in get_fund_ranking().values() for r in rows]


def ranking_by_isin(isin: str) -> FundRankRow | None:
    """The shortlist row for a fund by ISIN, or ``None`` if not in the shortlist."""
    return next((r for r in _all_rows() if r.isin == isin), None)


def peers_by_sub_category(sub_category: str, exclude_isin: str) -> list[FundRankRow]:
    """Shortlist funds of the same `sub_category` (like-for-like), sorted by rank,
    excluding the fund itself."""
    peers = [
        r for r in _all_rows()
        if r.sub_category == sub_category and r.isin != exclude_isin
    ]
    return sorted(peers, key=lambda r: r.rank)
