from __future__ import annotations

from decimal import Decimal

from Rebalancing.models import FundRowInput, RebalancingComputeRequest
from Rebalancing.steps import (
    step1_cap_and_spill,
    step2_compare_and_decide,
    step3_tax_classification,
    step4_initial_trades_under_stcg_cap,
    step5_loss_offset_top_up,
)


def _run_through_step5(request):
    s1, _, _ = step1_cap_and_spill.apply(request.rows, request)
    s2, _ = step2_compare_and_decide.apply(s1, request)
    s3 = step3_tax_classification.apply(s2, request)
    s4, _ = step4_initial_trades_under_stcg_cap.apply(s3, request)
    return step5_loss_offset_top_up.apply(s4, request)


def test_carryforward_loss_unlocks_undersold():
    """A 50k carryforward ST loss should unlock 50k of previously
    ST-budget-blocked sells in pass-2."""
    rows = [
        FundRowInput(
            asset_subgroup="low_beta_equities",
            sub_category="Large Cap Fund",
            recommended_fund="Held",
            isin="HELD",
            rank=1,
            target_amount_pre_cap=Decimal("100000"),
            present_allocation_inr=Decimal("200000"),
            st_value_inr=Decimal("200000"),
            st_cost_inr=Decimal("100000"),
            current_nav=Decimal("100"),
            fund_rating=8,
        ),
        FundRowInput(
            asset_subgroup="multi_asset",
            sub_category="Multi Cap Fund",
            recommended_fund="Buy",
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
        stcg_offset_budget_inr=Decimal(0),
        carryforward_st_loss_inr=Decimal("50000"),
        rows=rows,
    )
    s5 = _run_through_step5(req)
    held = next(r for r in s5 if r.isin == "HELD")
    # 50k carryforward → unlock 50k STCG → 100k of ST sold (at 50% gain ratio,
    # 100k value gives 50k STCG which exhausts the offset).
    assert held.pass2_sell_amount == Decimal("100000")
    assert held.final_holding_amount == held.holding_after_initial_trades - Decimal("100000")


def test_no_carryforward_no_pass2():
    """Without losses or carryforward, pass2_sell_amount stays 0."""
    rows = [
        FundRowInput(
            asset_subgroup="low_beta_equities",
            sub_category="Large Cap Fund",
            recommended_fund="A",
            isin="A",
            rank=1,
            target_amount_pre_cap=Decimal("100000"),
            present_allocation_inr=Decimal("100000"),
            fund_rating=8,
        ),
    ]
    req = RebalancingComputeRequest(
        total_corpus=Decimal("1000000"),
        tax_regime="new",
        effective_tax_rate_pct=20.0,
        rows=rows,
    )
    s5 = _run_through_step5(req)
    assert all(r.pass2_sell_amount == Decimal(0) for r in s5)
    assert all(r.final_holding_amount == r.holding_after_initial_trades for r in s5)
