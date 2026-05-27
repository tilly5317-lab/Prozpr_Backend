"""Pydantic schema — `onboarding.py`.

Request/response or DTO shapes for API validation and OpenAPI documentation. Kept separate from ORM models so API contracts can evolve independently of database columns.
"""


from __future__ import annotations

import uuid
from datetime import date
from typing import Optional

from pydantic import BaseModel, Field

from app.schemas.profile.personal import PersonalProfileResponse, PersonalProfileUpdate


class OnboardingProfileCreate(PersonalProfileUpdate):
    """Onboarding write — personal info + household finance (no duplicate scalars)."""


class OnboardingProfileResponse(PersonalProfileResponse):
    model_config = {"from_attributes": True}


class OnboardingCompleteRequest(BaseModel):
    is_complete: bool = True


class OtherAssetCreate(BaseModel):
    asset_name: str = Field(..., min_length=1, max_length=255)
    asset_type: Optional[str] = None
    current_value: Optional[float] = None


class OtherAssetResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    asset_name: str
    asset_type: Optional[str] = None
    current_value: Optional[float] = None


class OtherAssetBulkCreate(BaseModel):
    assets: list[OtherAssetCreate]
