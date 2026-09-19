"""Tests for the rebalancing chat facts' asset-class mixes.

``_asset_class_mix_from_buckets`` rolls fact-pack buckets into a lowercase
equity/debt/others ₹ mix via the SHARED rollup that also builds the Invest-page
bars. It is called twice per pack: once on ``current_inr`` for the current mix
(every bucket looked through on its own sub_category) and once on
``planned_final_inr`` for the target (the multi_asset sleeve keeps its engine
composition). Only genuinely blended SEBI categories are split.
"""

import pytest

from app.domains.rebalancing.services.rebal_engine.service import (
    _asset_class_mix_from_buckets,
)


def _bucket(sub_category, asset_subgroup, current_inr, planned_final_inr=0.0):
    return {
        "sub_category": sub_category,
        "asset_subgroup": asset_subgroup,
        "current_inr": current_inr,
        "planned_final_inr": planned_final_inr,
    }


def _current(buckets):
    return _asset_class_mix_from_buckets(
        buckets, amount_key="current_inr", multi_asset_sleeve=False
    )


def _target(buckets):
    return _asset_class_mix_from_buckets(
        buckets, amount_key="planned_final_inr", multi_asset_sleeve=True
    )


def test_blended_fund_is_split_per_category():
    mix = _current(
        [
            _bucket("Large Cap Fund", "low_beta_equities", 100.0),
            _bucket("Multi-Asset Allocation Fund", "medium_beta_equities", 100.0),
        ]
    )
    # Large cap 100 → equity; multi-asset 100 → 72.5/12.5/15.
    assert mix == pytest.approx({"equity": 172.5, "debt": 12.5, "others": 15.0})


def test_off_list_conservative_hybrid_still_splits():
    # An off-list Conservative Hybrid sits in short_debt (Debt) by subgroup, but
    # the per-bucket look-through keyed on sub_category still splits it 17.5/82.5.
    mix = _current([_bucket("Conservative Hybrid Fund", "short_debt", 100.0)])
    assert mix == pytest.approx({"equity": 17.5, "debt": 82.5, "others": 0.0})


def test_pure_funds_use_subgroup_class():
    mix = _current(
        [
            _bucket("Large Cap Fund", "low_beta_equities", 100.0),
            _bucket("Liquid Fund", "near_debt", 50.0),
            _bucket("Gold ETF", "gold_commodities", 25.0),
        ]
    )
    assert mix == pytest.approx({"equity": 100.0, "debt": 50.0, "others": 25.0})


def test_flexi_cap_is_pure_equity():
    # Flexi Cap is a pure-equity SEBI category. It used to carry a 72.5/17.5/10
    # band, which manufactured ~3% debt on an all-equity portfolio and made chat
    # disagree with the Invest page about the same holdings.
    mix = _current([_bucket("Flexi Cap Fund", "medium_beta_equities", 100.0)])
    assert mix == pytest.approx({"equity": 100.0, "debt": 0.0, "others": 0.0})


def test_current_does_not_apply_the_sleeve_composition():
    # A fund PARKED in the multi_asset subgroup is still whatever fund it is —
    # the 65/25/10 sleeve describes an allocation the plan has not filled yet,
    # not something the customer holds.
    mix = _current([_bucket("Flexi Cap Fund", "multi_asset", 100.0)])
    assert mix == pytest.approx({"equity": 100.0, "debt": 0.0, "others": 0.0})


def test_target_keeps_the_sleeve_composition():
    # On the TARGET the sleeve is split 65/25/10 regardless of which fund fills
    # it — otherwise an equity-heavy pick silently deletes the plan's debt.
    mix = _target([_bucket("Flexi Cap Fund", "multi_asset", 0.0, 100.0)])
    assert mix == pytest.approx({"equity": 65.0, "debt": 25.0, "others": 10.0})


def test_target_uses_planned_final_not_current():
    buckets = [
        _bucket("Large Cap Fund", "low_beta_equities", 100.0, 40.0),
        _bucket("Liquid Fund", "near_debt", 0.0, 60.0),
    ]
    assert _current(buckets) == pytest.approx(
        {"equity": 100.0, "debt": 0.0, "others": 0.0}
    )
    assert _target(buckets) == pytest.approx(
        {"equity": 40.0, "debt": 60.0, "others": 0.0}
    )
