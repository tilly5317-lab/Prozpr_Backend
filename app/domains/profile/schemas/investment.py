"""Investment objectives, horizon, and owned properties (``investment_profiles``)."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


class CurrentPropertyItem(BaseModel):
    """One owned property — replaces aggregate ``property_value`` / ``mortgage_*`` fields."""

    name: str = Field(min_length=1)
    property_value: float | None = Field(default=None, ge=0)
    has_mortgage: bool
    mortgage_emi: float | None = Field(default=None, ge=0)
    mortgage_end_date: date | None = None
    mortgage_balance: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _mortgage_fields(self) -> "CurrentPropertyItem":
        if self.has_mortgage:
            if self.mortgage_emi is None or self.mortgage_end_date is None:
                raise ValueError(
                    "mortgage_emi and mortgage_end_date are required when has_mortgage is true"
                )
        return self


class CurrentPropertyResponse(CurrentPropertyItem):
    model_config = ConfigDict(from_attributes=True)

    id: int


class CurrentPropertiesUpdate(BaseModel):
    """Full-replace write for a user's owned properties."""

    properties: list[CurrentPropertyItem] = Field(default_factory=list)


class InvestmentProfileUpdate(BaseModel):
    objectives: Optional[list[str]] = None
    detailed_goals: Optional[list[dict]] = None
    portfolio_value: Optional[float] = None
    target_corpus: Optional[float] = None
    target_timeline: Optional[str] = None
    retirement_age: Optional[int] = None
    expected_inflows: Optional[float] = None
    planned_major_expenses: Optional[float] = None
    emergency_fund: Optional[float] = None
    emergency_fund_months: Optional[str] = None
    liquidity_needs: Optional[str] = None
    income_needs: Optional[float] = None
    is_multi_phase_horizon: Optional[bool] = None
    phase_description: Optional[str] = None
    total_horizon: Optional[str] = None
    current_properties: Optional[list[CurrentPropertyItem]] = None


class InvestmentProfileResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    objectives: Optional[list[str]] = None
    detailed_goals: Optional[list[dict]] = None
    portfolio_value: Optional[float] = None
    target_corpus: Optional[float] = None
    target_timeline: Optional[str] = None
    retirement_age: Optional[int] = None
    expected_inflows: Optional[float] = None
    planned_major_expenses: Optional[float] = None
    emergency_fund: Optional[float] = None
    emergency_fund_months: Optional[str] = None
    liquidity_needs: Optional[str] = None
    income_needs: Optional[float] = None
    is_multi_phase_horizon: Optional[bool] = None
    phase_description: Optional[str] = None
    total_horizon: Optional[str] = None
    current_properties: list[CurrentPropertyResponse] = Field(default_factory=list)
    updated_at: Optional[datetime] = None
