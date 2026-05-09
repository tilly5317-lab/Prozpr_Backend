from __future__ import annotations

from decimal import Decimal

from Rebalancing.steps import (
    step1_cap_and_spill,
    step2_compare_and_decide,
    step3_tax_classification,
    step4_initial_trades_under_stcg_cap,
)


def _run_through_step4(request):
    s1, _, _ = step1_cap_and_spill.apply(request.rows, request)
    s2, _ = step2_compare_and_decide.apply(s1, request)
    s3 = step3_tax_classification.apply(s2, request)
    s4, warnings = step4_initial_trades_under_stcg_cap.apply(s3, request)
    return s4, warnings


def test_buys_match_sells_in_closed_system(request_with_holdings):
    s4, _ = _run_through_step4(request_with_holdings)
    total_buy = sum(r.pass1_buy_amount for r in s4)
    total_sell = sum(r.pass1_sell_amount for r in s4)
    # Buys are scaled to match available sells (forced + optional).
    # Forced sell = BAD1's 80k; optional from LC1 up to 1L excess.
    # Buy demand = MC1's 2L. So buys ≤ sells; buys cap at total_sell.
    assert total_buy <= total_sell
    # Forced exit fully sells BAD fund.
    bad = next(r for r in s4 if r.isin == "BAD1")
    assert bad.pass1_sell_amount == Decimal("80000")


def test_lt_priority_over_st(request_with_holdings):
    """Within a fund the engine sells LT before ST (LT is cheaper)."""
    s4, _ = _run_through_step4(request_with_holdings)
    lc1 = next(r for r in s4 if r.isin == "LC1")
    if lc1.pass1_sell_amount > 0:
        # First rupees should come from LT
        assert lc1.pass1_sell_lt_amount > 0


def test_stcg_budget_caps_pass1():
    """A budget of 0 disallows any positive STCG realisation in pass-1."""
    from Rebalancing.models import FundRowInput, RebalancingComputeRequest
    rows = [
        FundRowInput(
            asset_subgroup="low_beta_equities",
            sub_category="Large Cap Fund",
            recommended_fund="Held",
            isin="HELD",
            rank=1,
            target_amount_pre_cap=Decimal("100000"),
            present_allocation_inr=Decimal("200000"),  # 100k over target
            st_value_inr=Decimal("200000"),
            st_cost_inr=Decimal("100000"),  # 100k STCG
            current_nav=Decimal("100"),
            fund_rating=8,
        ),
        FundRowInput(
            asset_subgroup="multi_asset",
            sub_category="Multi Cap Fund",
            recommended_fund="Buy Target",
            isin="BUY",
            rank=1,
            target_amount_pre_cap=Decimal("200000"),
            present_allocation_inr=Decimal("100000"),
            fund_rating=9,
        ),
    ]
    req = RebalancingComputeRequest(
        total_corpus=Decimal("1000000"),
        tax_regime="new",
        effective_tax_rate_pct=20.0,
        stcg_offset_budget_inr=Decimal(0),  # zero budget
        rows=rows,
    )
    s4, warnings = _run_through_step4(req)
    held = next(r for r in s4 if r.isin == "HELD")
    # No STCG can be realised — sell is undersold.
    assert held.pass1_sell_st_amount == Decimal(0)
    assert held.pass1_undersell_due_to_stcg_cap > 0
    # Counterfactual: would have sold if budget were unlimited.
    assert held.pass1_sell_amount_no_stcg_cap > 0
    # Warning surfaced.
    binding = [w for w in warnings if w.code.value == "STCG_BUDGET_BINDING"]
    assert len(binding) == 1


def test_allocation_5_invariant(request_with_holdings):
    """allocation_5 = present + buy − sell."""
    s4, _ = _run_through_step4(request_with_holdings)
    for r in s4:
        expected = r.present_allocation_inr + r.pass1_buy_amount - r.pass1_sell_amount
        assert r.holding_after_initial_trades == expected
