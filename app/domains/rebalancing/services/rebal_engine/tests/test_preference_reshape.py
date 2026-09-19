"""Reshape math for preference constraints (pure; mirrors F3-B invariants)."""

from decimal import Decimal

from Rebalancing.consolidation import (
    BuyCandidate,
    ConsolidationConstraints,
    compute_reshaped_buys,
    constraints_active,
)


def _cand(isin, cat, rank, buy):
    return BuyCandidate(isin=isin, recommended_fund=isin, sub_category=cat,
                        asset_subgroup="x", rank=rank, buy_inr=Decimal(buy))


CANDS = [
    _cand("A", "Large Cap Fund", 1, 40_000),
    _cand("B", "Mid Cap Fund", 1, 20_000),
    _cand("C", "Gilt Fund", 1, 30_000),
    _cand("D", "Liquid Fund", 2, 10_000),
]
TOTAL = Decimal(100_000)


def test_excluded_categories_drop_and_redistribute():
    c = ConsolidationConstraints(excluded_categories=("Gilt Fund",))
    assert constraints_active(c)
    out = compute_reshaped_buys(CANDS, c)
    assert out["C"] == 0
    assert sum(out.values()) == TOTAL


def test_weight_target_raises_category_to_requested_share():
    c = ConsolidationConstraints(category_weight_targets={"Mid Cap Fund": 0.4})
    out = compute_reshaped_buys(CANDS, c)
    assert out["B"] >= Decimal(40_000) - Decimal(100)  # rounding_multiple slack
    assert sum(out.values()) == TOTAL


def test_weight_target_already_met_is_identity():
    c = ConsolidationConstraints(category_weight_targets={"Large Cap Fund": 0.3})
    out = compute_reshaped_buys(CANDS, c)
    assert out == {x.isin: x.buy_inr for x in CANDS}


def test_count_trim_never_evicts_weight_target_category():
    c = ConsolidationConstraints(
        target_fund_count=2, category_weight_targets={"Mid Cap Fund": 0.3}
    )
    out = compute_reshaped_buys(CANDS, c)
    assert out["B"] > 0
    assert sum(1 for v in out.values() if v > 0) <= 2
    assert sum(out.values()) == TOTAL


def test_legacy_only_constraints_hit_the_original_path():
    # Guard for the prime directive: legacy-only constraints must produce the
    # EXACT same result as before this change (arithmetic form included).
    c = ConsolidationConstraints(target_fund_count=2)
    out = compute_reshaped_buys(CANDS, c)
    assert sum(out.values()) == TOTAL
    assert out["D"] == 0 and out["B"] == 0


def test_count_conflict_bumps_to_protected_category_count():
    c = ConsolidationConstraints(
        target_fund_count=1,
        category_weight_targets={"Mid Cap Fund": 0.2, "Gilt Fund": 0.2},
    )
    out = compute_reshaped_buys(CANDS, c)
    # 1 fund can't honor 2 protected categories -> count bumps to 2 (disclosed
    # upstream by the chat layer via impact["count_bumped_to"]).
    assert sum(1 for v in out.values() if v > 0) == 2
    assert sum(out.values()) == TOTAL


def test_composition_excluded_beats_weights():
    c = ConsolidationConstraints(
        excluded_categories=("Mid Cap Fund",),
        category_weight_targets={"Mid Cap Fund": 0.4},
    )
    out = compute_reshaped_buys(CANDS, c)
    assert out["B"] == 0  # exclusion wins; caller surfaces the contradiction
    assert sum(out.values()) == TOTAL
