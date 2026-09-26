"""Tests for the fund-ranking lookup wrapper (by ISIN; peers by sub_category)."""

from __future__ import annotations

from collections import Counter

from app.domains.rebalancing.services.rebal_engine.fund_rank import get_fund_ranking
from app.domains.mutual_funds.services import fund_ranking_lookup as L


def _all_rows():
    return [r for rows in get_fund_ranking().values() for r in rows]


def test_by_isin_hit_and_miss():
    any_row = _all_rows()[0]
    hit = L.ranking_by_isin(any_row.isin)
    assert hit is not None and hit.fund_name == any_row.fund_name
    assert L.ranking_by_isin("NOT_AN_ISIN") is None


def test_peers_same_subcategory_excludes_self():
    rows = _all_rows()
    sub = next(s for s, c in Counter(r.sub_category for r in rows).items() if c >= 2)
    target = next(r for r in rows if r.sub_category == sub)

    peers = L.peers_by_sub_category(sub, exclude_isin=target.isin)

    assert target.isin not in {p.isin for p in peers}
    assert peers and all(p.sub_category == sub for p in peers)
    assert [p.rank for p in peers] == sorted(p.rank for p in peers)
