"""Invest-page bars rebuilt from per-fund audit rows with asset-class look-through.

Both bars must roll up ``fund_rows`` through the central ``add_to_asset_class_mix``
so a blended SEBI category (e.g. Flexi Cap) shows its real Equity/Debt/Others
split on the page, matching the dashboard donut and chat current-mix.
"""

from types import SimpleNamespace

import pytest

from app.domains.rebalancing.services.asset_class_breakdown import (
    fund_rows_current_mix,
    fund_rows_target_mix,
)


def _fr(sub_category, asset_subgroup, *, present=0.0, final=0.0):
    return SimpleNamespace(
        sub_category=sub_category,
        asset_subgroup=asset_subgroup,
        present_allocation_inr=present,
        final_holding_amount=final,
    )


def test_flexi_cap_current_is_looked_through():
    # A held Flexi Cap (a blended category) must split 72.5/17.5/10, not 100% equity.
    rows = [_fr("Flexi Cap Fund", "medium_beta_equities", present=100000.0)]
    mix = fund_rows_current_mix(rows)
    assert mix.get("Equity") == pytest.approx(72500.0)
    assert mix.get("Debt") == pytest.approx(17500.0)
    assert mix.get("Others") == pytest.approx(10000.0)


def test_pure_equity_fund_current_is_not_looked_through():
    # A single-class category (Large Cap) stays 100% equity via the subgroup fallback.
    rows = [_fr("Large Cap Fund", "low_beta_equities", present=100000.0)]
    mix = fund_rows_current_mix(rows)
    assert mix.get("Equity") == pytest.approx(100000.0)
    assert mix.get("Debt", 0.0) == pytest.approx(0.0)
    assert mix.get("Others", 0.0) == pytest.approx(0.0)


def test_target_uses_final_holding_amount():
    # The Target bar reads final_holding_amount, and looks through the same way.
    rows = [_fr("Flexi Cap Fund", "multi_asset", present=0.0, final=100000.0)]
    mix = fund_rows_target_mix(rows)
    assert mix.get("Equity") == pytest.approx(72500.0)
    assert mix.get("Debt") == pytest.approx(17500.0)
    assert mix.get("Others") == pytest.approx(10000.0)


def _sg(asset_subgroup, *, current=0.0, final=0.0):
    return SimpleNamespace(
        asset_subgroup=asset_subgroup,
        current_holding_inr=current,
        suggested_final_holding_inr=final,
    )


def test_breakdown_looks_through_flexi_from_fund_rows():
    # End-to-end: a run whose only holding/target is a Flexi Cap must render the
    # blended split on BOTH bars, not 100% equity from the subgroup total.
    from app.domains.rebalancing.routers.rebalancing_router import (
        _build_asset_class_breakdown,
    )

    run = SimpleNamespace(
        fund_rows=[
            _fr("Flexi Cap Fund", "medium_beta_equities", present=100000.0, final=100000.0)
        ],
        subgroup_summaries=[_sg("medium_beta_equities", current=100000.0, final=100000.0)],
        portfolio=None,
    )
    breakdown = _build_asset_class_breakdown(run)
    by_class = {row.asset_class: row for row in breakdown.rows}
    assert by_class["Equity"].current_inr == pytest.approx(72500.0)
    assert by_class["Debt"].current_inr == pytest.approx(17500.0)
    assert by_class["Others"].current_inr == pytest.approx(10000.0)
    assert by_class["Debt"].target_inr == pytest.approx(17500.0)
