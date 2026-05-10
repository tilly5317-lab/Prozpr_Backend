from __future__ import annotations

from decimal import Decimal

from Rebalancing.steps import step1_cap_and_spill, step2_compare_and_decide


def test_diff_and_exit_flags(request_with_holdings):
    s1, _, _ = step1_cap_and_spill.apply(
        request_with_holdings.rows, request_with_holdings
    )
    s2, warnings = step2_compare_and_decide.apply(s1, request_with_holdings)
    by_isin = {r.isin: r for r in s2}

    # LC1: target 4L − present 5L = −1L (sell)
    assert by_isin["LC1"].diff == Decimal("-100000")
    assert by_isin["LC1"].exit_flag is False
    assert by_isin["LC1"].worth_to_change is True

    # MC1: target 3L − present 1L = +2L (buy)
    assert by_isin["MC1"].diff == Decimal("200000")
    assert by_isin["MC1"].exit_flag is False
    assert by_isin["MC1"].worth_to_change is True

    # BAD1: target 0 − present 80k = −80k; exit_flag=True (not recommended)
    assert by_isin["BAD1"].diff == Decimal("-80000")
    assert by_isin["BAD1"].exit_flag is True
    assert by_isin["BAD1"].worth_to_change is True

    # BAD-detection warning emitted exactly once
    bad_warnings = [w for w in warnings if w.code.value == "BAD_FUND_DETECTED"]
    assert len(bad_warnings) == 1
    assert bad_warnings[0].affected_isins == ["BAD1"]


def test_low_rated_recommended_forces_exit():
    """A recommended fund with rating below the floor still gets exit_flag=True."""
    from Rebalancing.models import FundRowInput, RebalancingComputeRequest
    rows = [
        FundRowInput(
            asset_subgroup="low_beta_equities",
            sub_category="Large Cap Fund",
            recommended_fund="Low-rated",
            isin="LR",
            rank=1,
            target_amount_pre_cap=Decimal("100000"),
            present_allocation_inr=Decimal("100000"),
            fund_rating=3,  # below default EXIT_FLOOR_RATING=5
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
    assert s2[0].exit_flag is True
    assert s2[0].worth_to_change is True


def test_below_threshold_change_is_not_worth():
    """A tiny diff well below the threshold leaves worth_to_change False."""
    from Rebalancing.models import FundRowInput, RebalancingComputeRequest
    rows = [
        FundRowInput(
            asset_subgroup="low_beta_equities",
            sub_category="Large Cap Fund",
            recommended_fund="Stable",
            isin="ST",
            rank=1,
            target_amount_pre_cap=Decimal("100000"),
            present_allocation_inr=Decimal("99000"),  # 1% diff, below 10% threshold
            fund_rating=8,
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
    assert s2[0].worth_to_change is False
