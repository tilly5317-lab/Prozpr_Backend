"""Pydantic schema — `constraints.py`.

Request/response or DTO shapes for API validation and OpenAPI documentation. Kept separate from ORM models so API contracts can evolve independently of database columns.
"""


from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class AllocationConstraintItem(BaseModel):
    asset_class: str
    min_allocation: Optional[float] = None
    max_allocation: Optional[float] = None


class InvestmentConstraintUpdate(BaseModel):
    permitted_assets: Optional[list[str]] = None
    prohibited_instruments: Optional[list[str]] = None
    is_leverage_allowed: Optional[bool] = None
    is_derivatives_allowed: Optional[bool] = None
    diversification_notes: Optional[str] = None
    allocation_constraints: Optional[list[AllocationConstraintItem]] = None


class InvestmentConstraintResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    permitted_assets: Optional[list[str]] = None
    prohibited_instruments: Optional[list[str]] = None
    is_leverage_allowed: Optional[bool] = None
    is_derivatives_allowed: Optional[bool] = None
    diversification_notes: Optional[str] = None
    allocation_constraints: list[AllocationConstraintItem] = []
    updated_at: Optional[datetime] = None
