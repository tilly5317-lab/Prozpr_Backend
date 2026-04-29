"""Loads the production fund-rank CSV and exposes a typed lookup."""

from app.services.ai_bridge.rebalancing.fund_rank import (
    get_fund_ranking,
    FundRankRow,
)


def test_get_fund_ranking_returns_dict_keyed_by_subgroup():
    ranking = get_fund_ranking()
    assert isinstance(ranking, dict)
    assert "low_beta_equities" in ranking, "expected the canonical large-cap subgroup"
    rows = ranking["low_beta_equities"]
    assert all(isinstance(r, FundRankRow) for r in rows)


def test_ranks_are_sorted_ascending_within_subgroup():
    ranking = get_fund_ranking()
    for subgroup, rows in ranking.items():
        ranks = [r.rank for r in rows]
        assert ranks == sorted(ranks), f"{subgroup} ranks unsorted: {ranks}"
        assert ranks[0] == 1, f"{subgroup} doesn't start at rank 1"


def test_first_row_is_aditya_birla_large_cap():
    """Pin the canonical row 0 of the CSV to catch accidental file swaps."""
    ranking = get_fund_ranking()
    first = ranking["low_beta_equities"][0]
    assert first.rank == 1
    assert first.isin == "INF209K01YY7"
    assert first.sub_category == "Large Cap Fund"
