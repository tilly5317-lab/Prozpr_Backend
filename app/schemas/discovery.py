"""Pydantic schema — `discovery.py`.

Request/response or DTO shapes for API validation and OpenAPI documentation. Kept separate from ORM models so API contracts can evolve independently of database columns.
"""


from __future__ import annotations

import uuid
from typing import Optional

from pydantic import BaseModel


class FundResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    name: str
    short_name: Optional[str] = None
    ticker_symbol: Optional[str] = None
    category: Optional[str] = None
    sector: Optional[str] = None
    description: Optional[str] = None
    exchange: Optional[str] = None
    expense_ratio: Optional[float] = None
    exit_load: Optional[str] = None
    min_investment: Optional[float] = None
    return_1y: Optional[float] = None
    return_3y: Optional[float] = None
    return_5y: Optional[float] = None
    risk_level: Optional[str] = None
    is_trending: bool = False
    is_house_view: bool = False


class FundListResponse(BaseModel):
    funds: list[FundResponse]
    total: int


class SectorResponse(BaseModel):
    sector: str
    fund_count: int
