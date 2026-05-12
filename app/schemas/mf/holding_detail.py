"""Pydantic schema — `holding_detail.py`.

Response shape for the *MF holding detail page* — everything a fund-detail screen
needs in one call:

* scheme facts (from ``mf_fund_metadata``),
* the NAV time series (from ``mf_nav_history``) for the chart,
* the signed-in user's current position in the scheme (from ``portfolio_holdings``), and
* the user's transaction ledger in that scheme (from ``mf_transactions``), each row
  flagged ``is_inflow`` so the UI can colour buys/reinvestments green and
  redemptions/switch-outs red.

Served by ``GET /api/v1/mf/funds/{scheme_code}/holding-detail``
(``app/routers/mf/holding_detail.py`` → ``app/services/mf/mf_holding_detail_service.py``).
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from app.models.mf.enums import MfTransactionSource, MfTransactionType


class MfHoldingNavPoint(BaseModel):
    nav_date: date
    nav: float


class MfHoldingTransactionItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    transaction_date: date
    transaction_type: MfTransactionType  # BUY / SELL / SWITCH_IN / SWITCH_OUT / DIVIDEND_REINVEST
    folio_number: str
    units: float
    nav: float
    amount: float
    stamp_duty: Optional[float] = None
    source_system: MfTransactionSource
    # Convenience flags for the ledger UI — derived, not stored:
    is_inflow: bool  # True → units came IN (BUY / SWITCH_IN / DIVIDEND_REINVEST) → tint green; False → red
    signed_amount: float  # +amount when inflow, -abs(amount) when outflow


class MfHoldingPosition(BaseModel):
    """The user's current position in this scheme, summed across folios."""

    units: Optional[float] = None
    average_cost: Optional[float] = None  # per-unit, units-weighted across folios
    current_price: Optional[float] = None  # latest NAV the holdings sync recorded
    current_value: Optional[float] = None
    allocation_percentage: Optional[float] = None  # share of the whole portfolio
    invested_amount: Optional[float] = None  # average_cost * units, when both known
    unrealised_gain: Optional[float] = None  # current_value - invested_amount
    unrealised_gain_pct: Optional[float] = None
    folios: int = 0


class MfHoldingDetailResponse(BaseModel):
    # ---- scheme facts ----
    scheme_code: str
    scheme_name: Optional[str] = None
    amc_name: Optional[str] = None
    category: Optional[str] = None
    sub_category: Optional[str] = None
    isin: Optional[str] = None
    plan_type: Optional[str] = None
    option_type: Optional[str] = None
    # so the frontend can also call GET /mf/fund-metadata/{metadata_id}/investor-detail
    # for rolling-window returns + a downsampled performance chart, if it wants them.
    metadata_id: Optional[uuid.UUID] = None

    # ---- NAV time series (ascending by date) ----
    latest_nav: Optional[float] = None
    latest_nav_date: Optional[date] = None
    nav_history: list[MfHoldingNavPoint] = Field(default_factory=list)
    nav_history_from: Optional[date] = None
    nav_history_to: Optional[date] = None
    nav_history_truncated: bool = False  # True if the range had more rows than the cap

    # ---- NAV returns (point-to-point from ``mf_nav_history`` vs latest NAV in series) ----
    nav_returns_as_of: Optional[date] = Field(
        None,
        description="Latest NAV date used as the end point for the return figures below.",
    )
    nav_return_ytd_pct: Optional[float] = Field(
        None,
        description=(
            "Calendar YTD: first published NAV on or after 1 Jan through latest NAV in ``mf_nav_history``."
        ),
    )
    nav_return_6m_pct: Optional[float] = Field(
        None, description="Approx. 6-month return vs NAV on or before ~182 days ago."
    )
    nav_return_1y_pct: Optional[float] = None
    nav_return_3y_pct: Optional[float] = None
    nav_return_5y_pct: Optional[float] = None

    # ---- the user's position + ledger ----
    position: Optional[MfHoldingPosition] = None
    transactions: list[MfHoldingTransactionItem] = Field(default_factory=list)

    notes: list[str] = Field(default_factory=list)  # e.g. "No NAV history stored yet — sync NAVs."
