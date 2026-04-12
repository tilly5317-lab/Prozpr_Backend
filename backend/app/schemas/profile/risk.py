"""Pydantic schema — `risk.py`.

Request/response or DTO shapes for API validation and OpenAPI documentation. Kept separate from ORM models so API contracts can evolve independently of database columns.
"""


from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, computed_field

from app.models.profile.risk_profile import RISK_CATEGORIES


class RiskProfileUpdate(BaseModel):
    risk_level: Optional[int] = Field(None, ge=0, le=4)
    risk_willingness: Optional[float] = Field(None, ge=1.0, le=10.0)
    occupation_type: Optional[str] = Field(
        None,
        description="One of: public_sector, private_sector, family_business, commission_based, freelancer_gig, retired_homemaker_student",
    )
    risk_capacity: Optional[str] = None
    investment_experience: Optional[str] = None
    investment_horizon: Optional[str] = None
    drop_reaction: Optional[str] = None
    max_drawdown: Optional[float] = None
    comfort_assets: Optional[list[str]] = None


class RiskProfileResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    risk_level: Optional[int] = None
    risk_willingness: Optional[float] = None
    occupation_type: Optional[str] = None
    risk_capacity: Optional[str] = None
    investment_experience: Optional[str] = None
    investment_horizon: Optional[str] = None
    drop_reaction: Optional[str] = None
    max_drawdown: Optional[float] = None
    comfort_assets: Optional[list[str]] = None
    updated_at: Optional[datetime] = None

    @computed_field
    @property
    def risk_category(self) -> Optional[str]:
        if self.risk_level is not None and 0 <= self.risk_level <= 4:
            return RISK_CATEGORIES[self.risk_level]
        return None
