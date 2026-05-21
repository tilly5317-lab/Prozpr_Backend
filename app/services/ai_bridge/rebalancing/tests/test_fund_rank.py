"""Loads the production fund-rank CSV and exposes a typed lookup."""

from app.services.ai_bridge.rebalancing.fund_rank import (
    get_fund_ranking,
    get_rejection_reasons,
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


def test_first_row_low_beta_equities_pins_icici_prudential():
    """Pin the canonical row 0 of the CSV to catch accidental file swaps."""
    ranking = get_fund_ranking()
    first = ranking["low_beta_equities"][0]
    assert first.rank == 1
    assert first.isin == "INF109K016L0"
    assert first.sub_category == "Large Cap Fund"
    assert first.selection_reason, "rank-1 row should carry a selection_reason"


def test_get_rejection_reasons_for_known_isin():
    """Aditya Birla SL Large Cap (INF209K01YY7) is rank-blank in the new CSV
    with multiple rejection columns populated. The dict should join them into
    a single non-empty string."""
    reasons = get_rejection_reasons()
    assert "INF209K01YY7" in reasons, (
        "expected the rank-blank Aditya Birla SL Large Cap row to be loaded"
    )
    text = reasons["INF209K01YY7"]
    assert text, "rejection reason should be non-empty"
    # Substrings drawn from the CSV's pm_tenure_reason / returns_pctile_reason
    # / consistency_reason fields for this ISIN.
    assert "3 years" in text
    assert "top 25%" in text
