"""Pydantic schemas for RiskProfile read/update."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel


class RiskProfileUpdate(BaseModel):
    risk_level: Optional[int] = None
    risk_willingness: Optional[float] = None
    occupation_type: Optional[str] = None
    risk_capacity: Optional[str] = None
    investment_experience: Optional[str] = None
    investment_horizon: Optional[str] = None
    drop_reaction: Optional[str] = None
    max_drawdown: Optional[float] = None
    comfort_assets: Optional[list[Any]] = None


class RiskProfileResponse(BaseModel):
    model_config = {"from_attributes": True}

    risk_level: Optional[int] = None
    risk_willingness: Optional[float] = None
    risk_category: Optional[str] = None
    occupation_type: Optional[str] = None
    risk_capacity: Optional[str] = None
    investment_experience: Optional[str] = None
    investment_horizon: Optional[str] = None
    drop_reaction: Optional[str] = None
    max_drawdown: Optional[float] = None
    comfort_assets: Optional[list[Any]] = None
