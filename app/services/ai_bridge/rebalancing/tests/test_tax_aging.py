"""Per-lot tax-aging classification and exit-load counting."""

from datetime import date
from decimal import Decimal

from app.services.ai_bridge.rebalancing.holdings_ledger import Lot
from app.services.ai_bridge.rebalancing.tax_aging import (
    classify_lots_st_lt,
    count_units_in_exit_load_window,
)


def test_lt_lot_classified_as_long_term():
    """Equity lot held > 12 months → LT."""
    lot = Lot(
        acquisition_date=date(2024, 1, 1),
        units=Decimal("10"),
        acquisition_nav=Decimal("50"),
    )
    split = classify_lots_st_lt(
        [lot],
        asset_class="equity",
        current_nav=Decimal("60"),
        as_of=date(2026, 4, 28),
    )
    assert split.st_value_inr == Decimal(0)
    assert split.st_cost_inr == Decimal(0)
    assert split.lt_value_inr == Decimal("600")  # 10 * 60
    assert split.lt_cost_inr == Decimal("500")   # 10 * 50


def test_st_lot_just_under_12_months_equity():
    """Lot acquired 11 months ago is ST for equity (12-mo threshold)."""
    lot = Lot(
        acquisition_date=date(2025, 5, 28),
        units=Decimal("10"),
        acquisition_nav=Decimal("50"),
    )
    split = classify_lots_st_lt(
        [lot],
        asset_class="equity",
        current_nav=Decimal("60"),
        as_of=date(2026, 4, 28),
    )
    assert split.st_value_inr == Decimal("600")
    assert split.lt_value_inr == Decimal(0)


def test_debt_uses_24_month_threshold():
    """Debt lot at 18 months is still ST (24-mo threshold for debt)."""
    lot = Lot(
        acquisition_date=date(2024, 10, 28),
        units=Decimal("10"),
        acquisition_nav=Decimal("100"),
    )
    split = classify_lots_st_lt(
        [lot],
        asset_class="debt",
        current_nav=Decimal("105"),
        as_of=date(2026, 4, 28),
    )
    assert split.st_value_inr == Decimal("1050")
    assert split.lt_value_inr == Decimal(0)


def test_unknown_asset_class_defaults_to_equity_threshold():
    """Defensive: unrecognised asset_class behaves like equity (12 mo)."""
    lot = Lot(
        acquisition_date=date(2024, 1, 1),
        units=Decimal("10"),
        acquisition_nav=Decimal("50"),
    )
    split = classify_lots_st_lt(
        [lot],
        asset_class="hybrid",
        current_nav=Decimal("60"),
        as_of=date(2026, 4, 28),
    )
    assert split.lt_value_inr == Decimal("600")  # > 12 mo


def test_exit_load_window():
    """Lots within exit_load_months of as_of are counted."""
    lot_in = Lot(
        acquisition_date=date(2026, 1, 1),
        units=Decimal("4"),
        acquisition_nav=Decimal("100"),
    )
    lot_out = Lot(
        acquisition_date=date(2024, 1, 1),
        units=Decimal("6"),
        acquisition_nav=Decimal("80"),
    )
    units = count_units_in_exit_load_window(
        [lot_in, lot_out], exit_load_months=12, as_of=date(2026, 4, 28),
    )
    assert units == Decimal("4")


def test_exit_load_window_zero_months_returns_zero():
    """exit_load_months=0 → no lots in window, ever."""
    lot = Lot(
        acquisition_date=date(2026, 4, 27),
        units=Decimal("10"),
        acquisition_nav=Decimal("100"),
    )
    units = count_units_in_exit_load_window(
        [lot], exit_load_months=0, as_of=date(2026, 4, 28),
    )
    assert units == Decimal(0)
