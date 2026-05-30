"""Pydantic schemas for ``cashflow_input_assumptions``."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class CashflowAssumptionsBase(BaseModel):
    general_inflation: float = Field(ge=0.0, le=1.0)
    inflation_property: float = Field(ge=0.0, le=1.0)
    inflation_child_abroad_education: float = Field(ge=0.0, le=1.0)
    inflation_child_local_education: float = Field(ge=0.0, le=1.0)
    inflation_child_marriage: float = Field(ge=0.0, le=1.0)
    inflation_household_expense: float = Field(ge=0.0, le=1.0)
    inflation_post_retirement: float = Field(ge=0.0, le=1.0)
    annual_income_growth: float = Field(ge=0.0, le=1.0)
    annual_invested_amount_growth: float = Field(ge=0.0, le=1.0)
    roi_near_term_post_tax: float = Field(ge=0.0, le=1.0)
    roi_mid_term_post_tax: float = Field(ge=0.0, le=1.0)
    roi_long_term_post_tax: float = Field(ge=0.0, le=1.0)
    roi_retired_portfolio_annual: float = Field(ge=0.0, le=1.0)
    near_term_horizon_years: int = Field(ge=0)
    medium_term_horizon_years: int = Field(ge=0)
    default_mortgage_tenure_years: int = Field(gt=0)
    default_mortgage_interest_annual: float = Field(ge=0.0, le=1.0)
    default_property_downpayment_pct: float = Field(ge=0.0, le=1.0)
    default_sip_share: float = Field(ge=0.0, le=1.0)


class CashflowAssumptionsCreate(CashflowAssumptionsBase):
    pass


class CashflowAssumptionsUpdate(BaseModel):
    general_inflation: float | None = Field(default=None, ge=0.0, le=1.0)
    inflation_property: float | None = Field(default=None, ge=0.0, le=1.0)
    inflation_child_abroad_education: float | None = Field(default=None, ge=0.0, le=1.0)
    inflation_child_local_education: float | None = Field(default=None, ge=0.0, le=1.0)
    inflation_child_marriage: float | None = Field(default=None, ge=0.0, le=1.0)
    inflation_household_expense: float | None = Field(default=None, ge=0.0, le=1.0)
    inflation_post_retirement: float | None = Field(default=None, ge=0.0, le=1.0)
    annual_income_growth: float | None = Field(default=None, ge=0.0, le=1.0)
    annual_invested_amount_growth: float | None = Field(default=None, ge=0.0, le=1.0)
    roi_near_term_post_tax: float | None = Field(default=None, ge=0.0, le=1.0)
    roi_mid_term_post_tax: float | None = Field(default=None, ge=0.0, le=1.0)
    roi_long_term_post_tax: float | None = Field(default=None, ge=0.0, le=1.0)
    roi_retired_portfolio_annual: float | None = Field(default=None, ge=0.0, le=1.0)
    near_term_horizon_years: int | None = Field(default=None, ge=0)
    medium_term_horizon_years: int | None = Field(default=None, ge=0)
    default_mortgage_tenure_years: int | None = Field(default=None, gt=0)
    default_mortgage_interest_annual: float | None = Field(default=None, ge=0.0, le=1.0)
    default_property_downpayment_pct: float | None = Field(default=None, ge=0.0, le=1.0)
    default_sip_share: float | None = Field(default=None, ge=0.0, le=1.0)


class CashflowAssumptionsResponse(CashflowAssumptionsBase):
    model_config = ConfigDict(from_attributes=True)

    user_id: uuid.UUID
    created_at: datetime
    updated_at: datetime
