"""Fund catalog (AMFI scheme metadata)."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from app.models.mf.enums import MfOptionType, MfPlanType


class MfFundMetadataCreate(BaseModel):
    scheme_code: str = Field(..., max_length=20)
    isin: Optional[str] = Field(None, max_length=12)
    isin_div_reinvest: Optional[str] = Field(None, max_length=12)
    scheme_name: str = Field(..., max_length=200)
    amc_name: str = Field(..., max_length=100)
    category: str = Field(..., max_length=50)
    sub_category: Optional[str] = Field(None, max_length=100)
    plan_type: MfPlanType
    option_type: MfOptionType
    is_active: bool = True
    risk_rating_sebi: Optional[str] = Field(None, max_length=50)
    asset_class_sebi: Optional[str] = Field(None, max_length=100)
    asset_class: Optional[str] = Field(None, max_length=100)
    asset_subgroup: Optional[str] = Field(None, max_length=100)
    portfolio_managers_current: Optional[str] = None
    portfolio_managers_history: Optional[str] = None
    portfolio_manager_change_date: Optional[date] = None
    rating_external_agency_1: Optional[str] = Field(None, max_length=50)
    rating_external_agency_2: Optional[str] = Field(None, max_length=50)
    our_rating_parameter_1: Optional[str] = Field(None, max_length=100)
    our_rating_parameter_2: Optional[str] = Field(None, max_length=100)
    our_rating_parameter_3: Optional[str] = Field(None, max_length=100)
    our_rating_history_parameter_1: Optional[str] = None
    our_rating_history_parameter_2: Optional[str] = None
    our_rating_history_parameter_3: Optional[str] = None
    direct_plan_fees: Optional[float] = None
    regular_plan_fees: Optional[float] = None
    entry_load_percent: Optional[float] = None
    exit_load_percent: Optional[float] = None
    exit_load_months: Optional[int] = None
    large_cap_equity_pct: Optional[float] = None
    mid_cap_equity_pct: Optional[float] = None
    small_cap_equity_pct: Optional[float] = None
    debt_pct: Optional[float] = None
    others_pct: Optional[float] = None
    returns_1y_pct: Optional[float] = None
    returns_3y_pct: Optional[float] = None
    returns_5y_pct: Optional[float] = None
    returns_10y_pct: Optional[float] = None


class MfFundMetadataUpdate(BaseModel):
    isin: Optional[str] = Field(None, max_length=12)
    isin_div_reinvest: Optional[str] = Field(None, max_length=12)
    scheme_name: Optional[str] = Field(None, max_length=200)
    amc_name: Optional[str] = Field(None, max_length=100)
    category: Optional[str] = Field(None, max_length=50)
    sub_category: Optional[str] = Field(None, max_length=100)
    plan_type: Optional[MfPlanType] = None
    option_type: Optional[MfOptionType] = None
    is_active: Optional[bool] = None
    risk_rating_sebi: Optional[str] = Field(None, max_length=50)
    asset_class_sebi: Optional[str] = Field(None, max_length=100)
    asset_class: Optional[str] = Field(None, max_length=100)
    asset_subgroup: Optional[str] = Field(None, max_length=100)
    portfolio_managers_current: Optional[str] = None
    portfolio_managers_history: Optional[str] = None
    portfolio_manager_change_date: Optional[date] = None
    rating_external_agency_1: Optional[str] = Field(None, max_length=50)
    rating_external_agency_2: Optional[str] = Field(None, max_length=50)
    our_rating_parameter_1: Optional[str] = Field(None, max_length=100)
    our_rating_parameter_2: Optional[str] = Field(None, max_length=100)
    our_rating_parameter_3: Optional[str] = Field(None, max_length=100)
    our_rating_history_parameter_1: Optional[str] = None
    our_rating_history_parameter_2: Optional[str] = None
    our_rating_history_parameter_3: Optional[str] = None
    direct_plan_fees: Optional[float] = None
    regular_plan_fees: Optional[float] = None
    entry_load_percent: Optional[float] = None
    exit_load_percent: Optional[float] = None
    exit_load_months: Optional[int] = None
    large_cap_equity_pct: Optional[float] = None
    mid_cap_equity_pct: Optional[float] = None
    small_cap_equity_pct: Optional[float] = None
    debt_pct: Optional[float] = None
    others_pct: Optional[float] = None
    returns_1y_pct: Optional[float] = None
    returns_3y_pct: Optional[float] = None
    returns_5y_pct: Optional[float] = None
    returns_10y_pct: Optional[float] = None


class MfFundMetadataResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    scheme_code: str
    isin: Optional[str]
    isin_div_reinvest: Optional[str]
    scheme_name: str
    amc_name: str
    category: str
    sub_category: Optional[str]
    plan_type: MfPlanType
    option_type: MfOptionType
    is_active: bool
    risk_rating_sebi: Optional[str]
    asset_class_sebi: Optional[str]
    asset_class: Optional[str]
    asset_subgroup: Optional[str]
    portfolio_managers_current: Optional[str]
    portfolio_managers_history: Optional[str]
    portfolio_manager_change_date: Optional[date]
    rating_external_agency_1: Optional[str]
    rating_external_agency_2: Optional[str]
    our_rating_parameter_1: Optional[str]
    our_rating_parameter_2: Optional[str]
    our_rating_parameter_3: Optional[str]
    our_rating_history_parameter_1: Optional[str]
    our_rating_history_parameter_2: Optional[str]
    our_rating_history_parameter_3: Optional[str]
    direct_plan_fees: Optional[float]
    regular_plan_fees: Optional[float]
    entry_load_percent: Optional[float]
    exit_load_percent: Optional[float]
    exit_load_months: Optional[int]
    large_cap_equity_pct: Optional[float]
    mid_cap_equity_pct: Optional[float]
    small_cap_equity_pct: Optional[float]
    debt_pct: Optional[float]
    others_pct: Optional[float]
    returns_1y_pct: Optional[float]
    returns_3y_pct: Optional[float]
    returns_5y_pct: Optional[float]
    returns_10y_pct: Optional[float]
    created_at: datetime
    updated_at: datetime
