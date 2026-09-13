"""Look-through Equity/Debt/Commodity breakdown for an additional-investment plan.

The SIP + lump-sum "Proposed Target" bars show the deployment split across asset
classes. We REUSE the rebalancing rollup (``asset_class_mix_from_rows`` with the
``multi_asset`` sleeve rule) so AINV and rebalancing can never quote a different
split for the same funds — a plain per-subgroup rollup would call the whole
hybrid sleeve "Equity" and delete the plan's debt.
"""

from __future__ import annotations

from app.domains.additional_investment.services.additional_investment_read_service import (
    build_ainv_asset_class_breakdown,
)


def _rows_hybrid_heavy():
    """Mirrors a real lump-sum plan: most of the money in the ``multi_asset``
    sleeve (hybrids), plus mid-cap + US equity, and a small gold slice."""
    return [
        ("multi_asset", "Aggressive Hybrid Fund", 100000.0),
        ("multi_asset", "Flexi Cap Fund", 100000.0),
        ("multi_asset", "Dynamic Asset Allocation or Balanced Advantage", 100000.0),
        ("multi_asset", "Aggressive Hybrid Fund", 1900.0),
        ("medium_beta_equities", "Mid Cap Fund", 100000.0),
        ("us_equities", "FoF Overseas", 84800.0),
        ("gold_commodities", "Gold ETF", 5900.0),
    ]


def test_hybrid_heavy_plan_is_not_all_equity():
    b = build_ainv_asset_class_breakdown(_rows_hybrid_heavy())
    assert b is not None
    by_class = {r.asset_class: r.target_inr for r in b.rows}
    total = b.target_total_inr
    # The multi_asset sleeve is split 65/25/10, so Debt + Commodity(Others) must be
    # MATERIAL — the whole point of look-through (a plain rollup would be ~100% equity).
    assert by_class.get("Debt", 0.0) / total > 0.10
    assert by_class.get("Others", 0.0) > 0.0
    assert by_class["Equity"] == max(by_class.values())
    assert abs(sum(by_class.values()) - total) < 0.5


def test_multi_asset_sleeve_split_matches_engine_composition():
    # 3,01,900 in the multi_asset sleeve → 65/25/10; + 1,84,800 in equity funds;
    # + 5,900 gold. This is the exact split the customer's plan should show.
    b = build_ainv_asset_class_breakdown(_rows_hybrid_heavy())
    by_class = {r.asset_class: r.target_inr for r in b.rows}
    assert by_class["Debt"] == 75475.0  # 301900 * 0.25
    assert by_class["Others"] == 36090.0  # 301900 * 0.10 + 5900
    assert by_class["Equity"] == 381035.0  # 301900 * 0.65 + 100000 + 84800
    assert b.target_total_inr == 492600.0


def test_gold_only_maps_to_commodity_others():
    b = build_ainv_asset_class_breakdown([("gold_commodities", "Gold ETF", 5900.0)])
    assert [(r.asset_class, r.target_inr) for r in b.rows] == [("Others", 5900.0)]


def test_pure_equity_has_no_debt_or_others_rows():
    b = build_ainv_asset_class_breakdown(
        [
            ("medium_beta_equities", "Mid Cap Fund", 1000.0),
            ("us_equities", "FoF Overseas", 500.0),
        ]
    )
    assert [r.asset_class for r in b.rows] == ["Equity"]
    assert b.rows[0].target_inr == 1500.0


def test_rows_ordered_equity_debt_others():
    b = build_ainv_asset_class_breakdown(_rows_hybrid_heavy())
    assert [r.asset_class for r in b.rows] == ["Equity", "Debt", "Others"]


def test_current_inr_is_zero_for_a_deployment():
    b = build_ainv_asset_class_breakdown(_rows_hybrid_heavy())
    assert all(r.current_inr == 0.0 for r in b.rows)
    assert b.current_total_inr == 0.0


def test_empty_rows_returns_none():
    assert build_ainv_asset_class_breakdown([]) is None
