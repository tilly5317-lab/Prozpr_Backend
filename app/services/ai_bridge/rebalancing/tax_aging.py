"""Per-lot tax-aging and exit-load helpers.

Threshold values come from ``Rebalancing/config`` so the builder and the
engine share one source of truth for ST/LT cut-offs.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Iterable

from app.services.ai_bridge.common import ensure_ai_agents_path

ensure_ai_agents_path()

from Rebalancing.config import (  # type: ignore[import-not-found]  # noqa: E402
    ST_THRESHOLD_MONTHS_DEBT,
    ST_THRESHOLD_MONTHS_EQUITY,
)

from app.services.ai_bridge.rebalancing.holdings_ledger import Lot


@dataclass(frozen=True)
class LotSplit:
    """ST/LT split aggregated over a list of lots, all in INR."""

    st_value_inr: Decimal
    st_cost_inr: Decimal
    lt_value_inr: Decimal
    lt_cost_inr: Decimal


def _months_between(start: date, end: date) -> int:
    """Whole months elapsed from ``start`` to ``end`` (calendar-aware)."""
    return (end.year - start.year) * 12 + (end.month - start.month) - (
        1 if end.day < start.day else 0
    )


def _threshold_for(asset_class: str) -> int:
    if asset_class.lower() == "debt":
        return ST_THRESHOLD_MONTHS_DEBT
    return ST_THRESHOLD_MONTHS_EQUITY  # equity / others / unknown


def classify_lots_st_lt(
    lots: Iterable[Lot],
    *,
    asset_class: str,
    current_nav: Decimal,
    as_of: date,
) -> LotSplit:
    """Aggregate ST/LT value and cost for ``lots``.

    A lot whose age in months is *strictly less than* the threshold is ST.
    """
    threshold = _threshold_for(asset_class)
    st_value = st_cost = lt_value = lt_cost = Decimal(0)
    for lot in lots:
        age = _months_between(lot.acquisition_date, as_of)
        value = lot.units * current_nav
        cost = lot.units * lot.acquisition_nav
        if age < threshold:
            st_value += value
            st_cost += cost
        else:
            lt_value += value
            lt_cost += cost
    return LotSplit(
        st_value_inr=st_value,
        st_cost_inr=st_cost,
        lt_value_inr=lt_value,
        lt_cost_inr=lt_cost,
    )


def count_units_in_exit_load_window(
    lots: Iterable[Lot],
    *,
    exit_load_months: int,
    as_of: date,
) -> Decimal:
    """Sum units from lots whose age is *strictly less than* ``exit_load_months``."""
    if exit_load_months <= 0:
        return Decimal(0)
    total = Decimal(0)
    for lot in lots:
        if _months_between(lot.acquisition_date, as_of) < exit_load_months:
            total += lot.units
    return total
