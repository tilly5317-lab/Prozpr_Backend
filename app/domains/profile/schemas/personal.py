"""Personal info and household finance (``personal_finance_profiles`` + ``users``)."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class PersonalFinanceFields(BaseModel):
    """Canonical cashflow scalars — stored only on ``personal_finance_profiles``."""

    annual_income: Optional[float] = Field(default=None, ge=0)
    effective_tax_rate: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Blended post-deduction rate as a fraction (e.g. 0.22).",
    )
    financial_assets: Optional[float] = Field(default=None, ge=0)
    financial_liabilities_excl_mortgage: Optional[float] = Field(default=None, ge=0)
    monthly_household_expense: Optional[float] = Field(default=None, ge=0)
    starting_monthly_investment: Optional[float] = Field(default=None, ge=0)


class PersonalFinanceUpdate(PersonalFinanceFields):
    selected_goals: Optional[list[str]] = None
    custom_goals: Optional[list[str]] = None
    investment_horizon: Optional[str] = None
    wealth_sources: Optional[list[str]] = None
    personal_values: Optional[list[str]] = None


class PersonalFinanceResponse(PersonalFinanceFields):
    model_config = ConfigDict(from_attributes=True)

    user_id: uuid.UUID
    selected_goals: list[str] = []
    custom_goals: list[str] = []
    investment_horizon: Optional[str] = None
    wealth_sources: list[str] = []
    personal_values: list[str] = []
    updated_at: Optional[datetime] = None


class PersonalInfoUpdate(BaseModel):
    occupation: Optional[str] = None
    family_status: Optional[str] = None
    address: Optional[str] = None
    currency: Optional[str] = None
    date_of_birth: Optional[date] = None
    assumed_lifespan_years: Optional[int] = Field(default=None, ge=40, le=130)
    wealth_sources: Optional[list[str]] = None
    personal_values: Optional[list[str]] = None


class PersonalInfoResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    occupation: Optional[str] = None
    family_status: Optional[str] = None
    address: Optional[str] = None
    currency: str = "GBP"
    date_of_birth: Optional[date] = None
    assumed_lifespan_years: Optional[int] = None
    wealth_sources: list[str] = []
    personal_values: list[str] = []


class PersonalProfileUpdate(PersonalInfoUpdate, PersonalFinanceUpdate):
    """Single PATCH payload for onboarding / profile personal tab."""


class PersonalProfileResponse(PersonalInfoResponse, PersonalFinanceResponse):
    """Combined personal + household finance read model."""

    user_id: uuid.UUID
