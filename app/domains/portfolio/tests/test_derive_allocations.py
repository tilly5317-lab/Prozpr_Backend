"""Unit tests for the current-allocation donut's multi-asset look-through.

``_derive_allocations`` rolls holdings into the Equity/Debt/Others (+ Cash)
breakdown shown on the dashboard. Blended funds must split via the central
look-through; pure funds keep their single class; Cash carries forward; the
total portfolio value must be conserved.
"""

import uuid
from types import SimpleNamespace

import pytest

from app.domains.portfolio.routers.portfolio_router import _derive_allocations


def _holding(sub_category, current_value, *, name="Fund", itype="mutual_fund"):
    return SimpleNamespace(
        fund_metadata=SimpleNamespace(sub_category=sub_category),
        current_value=current_value,
        instrument_name=name,
        instrument_type=itype,
    )


def _cash(amount):
    return SimpleNamespace(asset_class="Cash", amount=amount)


def _by_class(rows):
    return {r.asset_class: r for r in rows}


def test_multi_asset_holding_splits_and_conserves_total():
    pid = uuid.uuid4()
    rows = _derive_allocations(
        pid,
        holdings=[
            _holding("Multi-Asset Allocation Fund", 100.0, name="X Multi Asset"),
            _holding("Large Cap Fund", 100.0, name="Y Large Cap"),
        ],
        persisted=[_cash(50.0)],
    )
    by = _by_class(rows)
    # Equity = 0.725*100 (multi) + 100 (large cap) = 172.5; Debt = 12.5; Others = 15; Cash = 50.
    assert by["Equity"].amount == pytest.approx(172.5)
    assert by["Debt"].amount == pytest.approx(12.5)
    assert by["Others"].amount == pytest.approx(15.0)
    assert by["Cash"].amount == pytest.approx(50.0)
    # Total conserved and percentages sum to 100.
    assert sum(r.amount for r in rows) == pytest.approx(250.0)
    assert sum(r.allocation_percentage for r in rows) == pytest.approx(100.0)
    assert by["Equity"].allocation_percentage == pytest.approx(69.0)


def test_conservative_hybrid_is_debt_heavy():
    pid = uuid.uuid4()
    rows = _derive_allocations(
        pid,
        holdings=[_holding("Conservative Hybrid Fund", 100.0, name="Z Conservative Hybrid")],
        persisted=[],
    )
    by = _by_class(rows)
    assert by["Equity"].amount == pytest.approx(17.5)
    assert by["Debt"].amount == pytest.approx(82.5)


def test_sub_categories_follow_the_lookthrough_split():
    """A blended fund's sub-category must appear under EVERY asset class it splits
    into, carrying the split amount — not sit whole under its dominant class.

    This is what the donut's slice drill-down renders, so each bucket's
    sub-category amounts must sum back to that bucket's own total.
    """
    pid = uuid.uuid4()
    rows = _derive_allocations(
        pid,
        holdings=[
            _holding("Aggressive Hybrid Fund", 100.0, name="X Aggressive Hybrid"),
            _holding("Large Cap Fund", 100.0, name="Y Large Cap"),
        ],
        persisted=[_cash(50.0)],
    )
    by = _by_class(rows)
    subs = lambda r: {s.name: s.amount for s in r.sub_categories}  # noqa: E731

    assert subs(by["Equity"]) == {
        "Aggressive Hybrid Fund": pytest.approx(72.5),
        "Large Cap Fund": pytest.approx(100.0),
    }
    assert subs(by["Debt"]) == {"Aggressive Hybrid Fund": pytest.approx(17.5)}
    assert subs(by["Others"]) == {"Aggressive Hybrid Fund": pytest.approx(10.0)}
    # Cash has no holding behind it, so it has nothing to break down.
    assert by["Cash"].sub_categories == []
    # Every bucket's rows must reconcile to the headline amount above them.
    for r in rows:
        if r.asset_class == "Cash":
            continue
        assert sum(s.amount for s in r.sub_categories) == pytest.approx(r.amount)


def test_pure_funds_unchanged():
    pid = uuid.uuid4()
    rows = _derive_allocations(
        pid,
        holdings=[
            _holding("Large Cap Fund", 100.0),
            _holding("Liquid Fund", 100.0),
        ],
        persisted=[],
    )
    by = _by_class(rows)
    assert by["Equity"].amount == pytest.approx(100.0)
    assert by["Debt"].amount == pytest.approx(100.0)
    assert "Others" not in by
