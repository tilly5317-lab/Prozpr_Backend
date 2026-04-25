from __future__ import annotations

from decimal import Decimal

from Rebalancing.steps import step1_cap_and_spill


def test_overflow_spills_into_next_rank(empty_holdings_request):
    rows, warnings, unrebalanced = step1_cap_and_spill.apply(
        empty_holdings_request.rows, empty_holdings_request
    )
    by_rank = {r.rank: r for r in rows}
    # Multi-cap cap = 20% × 50L = 10L. Rank-1 wanted 12.5L → cap at 10L,
    # 2.5L spills to rank-2.
    assert by_rank[1].final_target_amount == Decimal("1000000")
    assert by_rank[2].final_target_amount == Decimal("250000")
    assert by_rank[3].final_target_amount == Decimal("0")
    assert unrebalanced == Decimal(0)
    assert warnings == []


def test_pcts_match_amounts(empty_holdings_request):
    rows, _, _ = step1_cap_and_spill.apply(
        empty_holdings_request.rows, empty_holdings_request
    )
    by_rank = {r.rank: r for r in rows}
    # 10L of 50L corpus = 20%; 2.5L = 5%
    assert by_rank[1].final_target_pct == 20.0
    assert by_rank[2].final_target_pct == 5.0
    assert by_rank[1].max_pct == 20.0


def test_residual_warning_when_all_ranks_capped():
    # Force a residual: 50L corpus, all ranks at "Large Cap Fund" (10% cap),
    # rank-1 wants 40L → 10L cap → 30L spills → rank-2 cap 10L → 20L spills →
    # rank-3 cap 10L → 10L residual.
    from Rebalancing.models import FundRowInput, RebalancingComputeRequest
    rows = [
        FundRowInput(
            asset_subgroup="low_beta_equities",
            sub_category="Large Cap Fund",
            recommended_fund=f"LC {i}",
            isin=f"LC_{i}",
            rank=i,
            target_amount_pre_cap=Decimal("4000000") if i == 1 else Decimal(0),
        )
        for i in (1, 2, 3)
    ]
    req = RebalancingComputeRequest(
        total_corpus=Decimal("5000000"),
        tax_regime="new",
        effective_tax_rate_pct=20.0,
        rows=rows,
    )
    out, warnings, unrebalanced = step1_cap_and_spill.apply(req.rows, req)
    by_rank = {r.rank: r for r in out}
    assert by_rank[1].final_target_amount == Decimal("500000")
    assert by_rank[2].final_target_amount == Decimal("500000")
    assert by_rank[3].final_target_amount == Decimal("500000")
    assert unrebalanced == Decimal("2500000")
    assert len(warnings) == 1
    assert warnings[0].code.value == "UNREBALANCED_REMAINDER"


def test_bad_rows_excluded_from_spill(request_with_holdings):
    out, _, _ = step1_cap_and_spill.apply(
        request_with_holdings.rows, request_with_holdings
    )
    bad = next(r for r in out if r.isin == "BAD1")
    assert bad.final_target_amount == Decimal(0)
    assert bad.rank == 0
    assert bad.is_recommended is False
