"""Tests for chat's current-mix narration look-through.

``compute_current_asset_class_mix`` derives Equity/Debt/Others live from holdings
(splitting blended funds via the shared look-through) and carries Cash from the
persisted allocation rows. It must agree with the dashboard donut and fall back
to the frozen allocation rows when a portfolio has no holdings.
"""

from types import SimpleNamespace

import pytest

from app.domains.asset_allocation.services.aa_engine.service import (
    compute_current_asset_class_mix,
)


def _holding(sub_category, current_value, *, name="Fund", itype="mutual_fund"):
    return SimpleNamespace(
        fund_metadata=SimpleNamespace(sub_category=sub_category),
        current_value=current_value,
        instrument_name=name,
        instrument_type=itype,
    )


def _alloc(asset_class, amount):
    return SimpleNamespace(asset_class=asset_class, amount=amount)


def _user(holdings, allocations, *, is_primary=True):
    primary = SimpleNamespace(
        is_primary=is_primary, holdings=holdings, allocations=allocations
    )
    return SimpleNamespace(portfolios=[primary])


def test_holdings_drive_mix_with_lookthrough_and_cash_carried():
    user = _user(
        holdings=[
            _holding("Multi-Asset Allocation Fund", 100.0, name="X Multi Asset"),
            _holding("Large Cap Fund", 100.0, name="Y Large Cap"),
        ],
        allocations=[_alloc("Cash", 50.0), _alloc("Equity", 999.0)],  # Equity row ignored — derived from holdings
    )
    mix = compute_current_asset_class_mix(user)
    assert mix["inr"] == pytest.approx(
        {"equity": 172.5, "debt": 12.5, "cash": 50.0, "others": 15.0}
    )
    assert mix["pct"] == {"equity": 69, "debt": 5, "cash": 20, "others": 6}


def test_falls_back_to_allocation_rows_when_no_holdings():
    user = _user(
        holdings=[],
        allocations=[_alloc("Equity", 100.0), _alloc("Debt", 50.0), _alloc("Cash", 25.0)],
    )
    mix = compute_current_asset_class_mix(user)
    assert mix["inr"] == pytest.approx(
        {"equity": 100.0, "debt": 50.0, "cash": 25.0, "others": 0.0}
    )


def test_returns_none_without_portfolio():
    assert compute_current_asset_class_mix(SimpleNamespace(portfolios=[])) is None
    assert compute_current_asset_class_mix(_user(holdings=[], allocations=[])) is None
