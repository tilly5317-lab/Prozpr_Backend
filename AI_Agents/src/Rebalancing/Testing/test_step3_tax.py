from __future__ import annotations

from decimal import Decimal

from Rebalancing.steps import (
    step1_cap_and_spill,
    step2_compare_and_decide,
    step3_tax_classification,
)


def test_sell_candidates_get_tax_breakdown(request_with_holdings):
    s1, _, _ = step1_cap_and_spill.apply(
        request_with_holdings.rows, request_with_holdings
    )
    s2, _ = step2_compare_and_decide.apply(s1, request_with_holdings)
    s3 = step3_tax_classification.apply(s2, request_with_holdings)
    by_isin = {r.isin: r for r in s3}

    # LC1 is a sell candidate. STCG = 200k − 200k = 0; LTCG = 300k − 250k = 50k.
    assert by_isin["LC1"].stcg_amount == Decimal(0)
    assert by_isin["LC1"].ltcg_amount == Decimal("50000")
    assert by_isin["LC1"].exit_load_amount == Decimal(0)  # no exit load configured

    # MC1 is a buy candidate, no tax computed.
    assert by_isin["MC1"].stcg_amount == Decimal(0)
    assert by_isin["MC1"].ltcg_amount == Decimal(0)


def test_exit_load_applies_to_in_period_value():
    """Fund with units in exit-load period accumulates a non-zero potential load."""
    from Rebalancing.models import FundRowInput, RebalancingComputeRequest
    rows = [
        FundRowInput(
            asset_subgroup="low_beta_equities",
            sub_category="Large Cap Fund",
            recommended_fund="Loaded",
            isin="LD",
            rank=1,
            target_amount_pre_cap=Decimal("0"),  # full sell
            present_allocation_inr=Decimal("100000"),
            st_value_inr=Decimal("50000"),
            st_cost_inr=Decimal("48000"),
            lt_value_inr=Decimal("50000"),
            lt_cost_inr=Decimal("40000"),
            current_nav=Decimal("100"),
            units_within_exit_load_period=Decimal("300"),  # 30k value in load period
            exit_load_pct=1.0,
            fund_rating=2,  # forces exit
            is_recommended=True,
        ),
    ]
    req = RebalancingComputeRequest(
        total_corpus=Decimal("1000000"),
        tax_regime="new",
        effective_tax_rate_pct=20.0,
        rows=rows,
    )
    s1, _, _ = step1_cap_and_spill.apply(req.rows, req)
    s2, _ = step2_compare_and_decide.apply(s1, req)
    s3 = step3_tax_classification.apply(s2, req)
    # Exit load = 30k × 1% = 300
    assert s3[0].exit_load_amount == Decimal("300")
