"""Pydantic schemas for InvestmentConstraint and AllocationConstraint read/update."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel


class AllocationConstraintItem(BaseModel):
    model_config = {"from_attributes": True}

    asset_class: str
    min_allocation: Optional[float] = None
    max_allocation: Optional[float] = None


class InvestmentConstraintUpdate(BaseModel):
    permitted_assets: Optional[list[Any]] = None
    prohibited_instruments: Optional[list[Any]] = None
    is_leverage_allowed: Optional[bool] = None
    is_derivatives_allowed: Optional[bool] = None
    diversification_notes: Optional[str] = None
    allocation_constraints: Optional[list[AllocationConstraintItem]] = None


class InvestmentConstraintResponse(BaseModel):
    model_config = {"from_attributes": True}

    permitted_assets: Optional[list[Any]] = None
    prohibited_instruments: Optional[list[Any]] = None
    is_leverage_allowed: Optional[bool] = None
    is_derivatives_allowed: Optional[bool] = None
    diversification_notes: Optional[str] = None
    allocation_constraints: list[AllocationConstraintItem] = []
