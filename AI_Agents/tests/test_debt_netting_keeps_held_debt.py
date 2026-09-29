"""A held debt fund is never sold to buy another debt fund; debt with no matching
debt buy still sells, and force-exits still exit."""

from __future__ import annotations

from decimal import Decimal

from practical_asset_allocation.pipeline import PracticalAllocationInput
from Rebalancing.config import FORCE_EXIT_RANK
from Rebalancing.models import FundRowInput, RebalancingComputeRequest
from Rebalancing.steps import (
    step1_cap_and_spill,
    step2_compare_and_decide,
    step2b_suppress_debt_switch,
)

CORPUS = Decimal("10000000")


def _row(isin, subgroup, rank, target, present, lt=None, **kw):
    lt = present if lt is None else lt
    return FundRowInput(
        asset_subgroup=subgroup, sub_category=kw.pop("sub_category", "Liquid Fund"),
        recommended_fund=f"Fund {isin}", isin=isin, rank=rank,
        target_amount_pre_cap=Decimal(target), present_allocation_inr=Decimal(present),
        invested_cost_inr=Decimal(present) * Decimal("0.85"),
        lt_value_inr=Decimal(lt), lt_cost_inr=Decimal(lt) * Decimal("0.85"),
        current_nav=Decimal("100"), fund_rating=8, **kw,
    )


def _step2b(rows):
    inp = PracticalAllocationInput(
        effective_risk_score=5.5, age=40, annual_income=2_000_000, osi=0.0,
        savings_rate_adjustment="none", gap_exceeds_3=False, shortfall_amount=0.0,
        total_corpus=float(CORPUS), monthly_household_expense=100_000,
        effective_tax_rate=15.0, financial_assets=float(CORPUS), goals=[],
        mf_corpus=float(CORPUS), non_mf_equity_corpus=0, elss_corpus=0,
    )
    req = RebalancingComputeRequest(
        practical_allocation_input=inp, tax_regime="new", effective_tax_rate_pct=30.0, rows=rows,
    )
    s1, _, _ = step1_cap_and_spill.apply(req.rows, req)
    s2, _ = step2_compare_and_decide.apply(s1, req)
    out, _ = step2b_suppress_debt_switch.apply(s2, req)
    return {r.isin: r for r in out}


def test_off_list_liquid_fund_is_kept_against_a_debt_buy():
    by_isin = _step2b([
        _row("LIQ", "near_debt", 0, "400000", "1000000", lt="600000", is_recommended=False),
        _row("ARB", "arbitrage", 1, "800000", "0", sub_category="Arbitrage Fund"),
    ])
    assert by_isin["LIQ"].diff == Decimal(0)
    assert by_isin["LIQ"].worth_to_change is False
    assert by_isin["ARB"].diff == Decimal("200000")


def test_off_list_debt_with_no_debt_buy_still_sells():
    by_isin = _step2b([
        _row("LIQ", "near_debt", 0, "400000", "1000000", lt="600000", is_recommended=False),
        _row("EQ", "low_beta_equities", 1, "600000", "0", sub_category="Large Cap Fund"),
    ])
    assert by_isin["LIQ"].diff == Decimal("-600000")
    assert by_isin["LIQ"].worth_to_change is True


def test_force_exit_debt_still_exits():
    by_isin = _step2b([
        _row("BAD", "short_debt", FORCE_EXIT_RANK, "0", "500000", is_recommended=False),
        _row("ARB", "arbitrage", 1, "800000", "0", sub_category="Arbitrage Fund"),
    ])
    assert by_isin["BAD"].exit_flag is True
    assert by_isin["BAD"].diff == Decimal("-500000")
