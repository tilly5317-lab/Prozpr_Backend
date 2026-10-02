"""A fund row trades only when |target − present| reaches min(1% of the
portfolio, 50% of max(target, present)), or when it is flagged for exit."""

from __future__ import annotations

from decimal import Decimal

import pytest

from practical_asset_allocation.pipeline import (
    PracticalAllocationInput,
    run_practical_allocation,
)
from Rebalancing.config import EXIT_FLOOR_RATING, FORCE_EXIT_RANK
from Rebalancing.models import FundRowInput, KnobSnapshot, RebalancingComputeRequest
from Rebalancing.steps import (
    step1_cap_and_spill,
    step2_compare_and_decide,
    step2b_suppress_debt_switch,
    step3_tax_classification,
    step4_initial_trades_under_stcg_cap,
    step5_loss_offset_top_up,
    step6_presentation,
)
from Rebalancing.steps.step6_presentation import _build_knob_snapshot

CORPUS = Decimal("10000000")  # ₹1 crore


def _row(isin, subgroup, target, present, rating=8, rank=1, is_recommended=True):
    return FundRowInput(
        asset_subgroup=subgroup, sub_category="Large Cap Fund",
        recommended_fund=f"Fund {isin}", isin=isin, rank=rank,
        target_amount_pre_cap=Decimal(target), present_allocation_inr=Decimal(present),
        invested_cost_inr=Decimal(present) * Decimal("0.85"),
        lt_value_inr=Decimal(present), lt_cost_inr=Decimal(present) * Decimal("0.85"),
        current_nav=Decimal("100"), fund_rating=rating, is_recommended=is_recommended,
    )


def _request(rows):
    inp = PracticalAllocationInput(
        effective_risk_score=5.5, age=40, annual_income=2_000_000, osi=0.0,
        savings_rate_adjustment="none", gap_exceeds_3=False, shortfall_amount=0.0,
        total_corpus=float(CORPUS), monthly_household_expense=100_000,
        effective_tax_rate=15.0, goals=[],
    )
    return RebalancingComputeRequest(
        practical_allocation_input=inp, tax_regime="new", effective_tax_rate_pct=30.0,
        rows=rows,
    )


def test_step2_trades_only_past_the_lower_of_the_two_bars():
    req = _request([
        _row("BIG", "low_beta_equities", "2850000", "3000000"),    # -1.5L vs bar 1L
        _row("MID", "medium_beta_equities", "560000", "500000"),   # +60k vs bar 1L
        _row("SMALL", "high_beta_equities", "130000", "100000"),   # +30k vs bar 65k
        _row("NEW", "value_equities", "200000", "0"),
        _row("OUT", "us_equities", "0", "400000"),
        _row("BAD", "sector_equities", "50000", "50000", rating=EXIT_FLOOR_RATING - 1),
    ])
    s1, _, _ = step1_cap_and_spill.apply(req.rows, req)
    s2, _ = step2_compare_and_decide.apply(s1, req)
    worth = {r.isin: r.worth_to_change for r in s2}
    assert worth == {
        "BIG": True, "MID": False, "SMALL": False, "NEW": True, "OUT": True, "BAD": True,
    }


def _through_step4(rows):
    req = _request(rows)
    s1, _, _ = step1_cap_and_spill.apply(req.rows, req)
    s2, _ = step2_compare_and_decide.apply(s1, req)
    s2b, _ = step2b_suppress_debt_switch.apply(s2, req)
    s3 = step3_tax_classification.apply(s2b, req)
    s4, _ = step4_initial_trades_under_stcg_cap.apply(s3, req)
    return {r.isin: r for r in s4}


def _exit(isin, subgroup, present):
    return _row(isin, subgroup, "0", present, rank=FORCE_EXIT_RANK, is_recommended=False)


def test_forced_exit_money_reaches_a_buy_under_the_bar():
    by = _through_step4([
        _row("A", "low_beta_equities", "580000", "500000"),   # +80k, under the 1L bar
        _exit("X", "low_beta_equities", "80000"),
    ])
    assert by["X"].pass1_sell_amount == Decimal("80000")
    assert by["A"].worth_to_change is False
    assert by["A"].pass1_buy_amount == Decimal("80000")


def test_leftover_goes_biggest_gap_first_and_never_past_a_gap():
    by = _through_step4([
        _row("A", "low_beta_equities", "580000", "500000"),     # gap 80k
        _row("C", "medium_beta_equities", "450000", "400000"),  # gap 50k
        _exit("X", "us_equities", "100000"),
    ])
    assert by["A"].pass1_buy_amount == Decimal("80000")
    assert by["C"].pass1_buy_amount == Decimal("20000")


def test_buys_that_clear_the_bar_are_funded_before_the_leftover():
    by = _through_step4([
        _row("N", "value_equities", "60000", "0"),              # new fund: clears the bar
        _row("A", "low_beta_equities", "580000", "500000"),     # under the bar
        _exit("X", "us_equities", "150000"),
    ])
    assert by["N"].pass1_buy_amount == Decimal("60000")
    assert by["A"].pass1_buy_amount == Decimal("80000")


def _through_step6(rows):
    req = _request(rows)
    s1, _, unrebalanced = step1_cap_and_spill.apply(req.rows, req)
    s2, w2 = step2_compare_and_decide.apply(s1, req)
    s2b, w2b = step2b_suppress_debt_switch.apply(s2, req)
    s3 = step3_tax_classification.apply(s2b, req)
    s4, w4 = step4_initial_trades_under_stcg_cap.apply(s3, req)
    s5 = step5_loss_offset_top_up.apply(s4, req)
    practical = run_practical_allocation(req.practical_allocation_input)
    response = step6_presentation.apply(
        s5, req, w2 + w2b + w4, unrebalanced, practical=practical
    )
    return {t.isin: t for t in response.trade_list}


def test_a_leftover_funded_buy_is_never_labelled_a_split():
    trades = _through_step6([
        _row("A1", "low_beta_equities", "700000", "500000", rank=1),  # +200k, clears the bar
        _row("A2", "low_beta_equities", "580000", "500000", rank=2),  # +80k, under the bar
        _exit("X", "us_equities", "280000"),
    ])
    assert trades["A2"].reason_code == "add_to_target"
    assert trades["A1"].reason_code == "add_to_target"


def test_the_run_snapshot_records_both_knobs():
    from Rebalancing import config

    snap = _build_knob_snapshot()
    assert snap.rebalance_min_change_portfolio_pct == config.REBALANCE_MIN_CHANGE_PORTFOLIO_PCT
    assert snap.rebalance_min_change_fund_pct == config.REBALANCE_MIN_CHANGE_FUND_PCT


def test_a_snapshot_saved_before_these_knobs_still_loads():
    old = _build_knob_snapshot().model_dump(mode="json")
    old.pop("rebalance_min_change_portfolio_pct")
    old.pop("rebalance_min_change_fund_pct")
    old["rebalance_min_change_pct"] = 0.10
    snap = KnobSnapshot.model_validate(old)
    assert snap.rebalance_min_change_portfolio_pct is None
    assert snap.rebalance_min_change_fund_pct is None


@pytest.mark.parametrize("raw", ["1", "0", "-0.01", "nan", "1.5"])
def test_a_threshold_knob_outside_zero_to_one_fails_at_startup(monkeypatch, raw):
    from Rebalancing.config import _fraction_env

    monkeypatch.setenv("REBAL_TEST_FRACTION", raw)
    with pytest.raises(ValueError, match="REBAL_TEST_FRACTION"):
        _fraction_env("REBAL_TEST_FRACTION", "0.01")


def test_a_threshold_knob_inside_zero_to_one_is_read():
    from Rebalancing.config import _fraction_env

    assert _fraction_env("REBAL_TEST_FRACTION_UNSET", "0.01") == 0.01
