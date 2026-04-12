"""Pydantic schema — `onboarding.py`.

Request/response or DTO shapes for API validation and OpenAPI documentation. Kept separate from ORM models so API contracts can evolve independently of database columns.
"""


from __future__ import annotations

import uuid
from datetime import date
from typing import Optional

from pydantic import BaseModel, Field


class OnboardingProfileCreate(BaseModel):
    date_of_birth: Optional[date] = None
    selected_goals: list[str] = Field(default_factory=list)
    custom_goals: list[str] = Field(default_factory=list)
    investment_horizon: Optional[str] = None
    annual_income_min: Optional[float] = None
    annual_income_max: Optional[float] = None
    annual_expense_min: Optional[float] = None
    annual_expense_max: Optional[float] = None


class OnboardingProfileResponse(BaseModel):
    model_config = {"from_attributes": True}

    user_id: uuid.UUID
    date_of_birth: Optional[date] = None
    selected_goals: list[str] = []
    custom_goals: list[str] = []
    investment_horizon: Optional[str] = None
    annual_income_min: Optional[float] = None
    annual_income_max: Optional[float] = None
    annual_expense_min: Optional[float] = None
    annual_expense_max: Optional[float] = None


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
