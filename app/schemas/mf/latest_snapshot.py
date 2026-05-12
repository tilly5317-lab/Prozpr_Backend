"""Schemas for user_mf_latest_snapshot holdings payloads."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict


class UserMfLatestSnapshotResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    scheme_code: str
    isin: Optional[str]
    fund_name: Optional[str]
    amc_name: Optional[str]
    category: Optional[str]
    sub_category: Optional[str]
    sub_group: Optional[str]
    invested_amount: float
    current_units: float
    avg_nav: Optional[float]
    current_nav: Optional[float]
    current_value: float
    unrealized_pnl: float
    absolute_return_pct: Optional[float]
    xirr_pct: Optional[float]
    portfolio_weight_pct: Optional[float]
    return_1y_pct: Optional[float]
    return_3y_pct: Optional[float]
    return_5y_pct: Optional[float]
    first_investment_date: Optional[date]
    last_transaction_date: Optional[date]
    nav_date: Optional[date]
    transactions_count: int
    folio_number: Optional[str]
    updated_at: datetime
