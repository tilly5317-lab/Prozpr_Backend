"""Fund catalog (AMFI scheme metadata)."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import List, Optional

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


class MfFundMetadataListItem(BaseModel):
    """Slim row used by the Discover search/explorer list — keeps the payload
    light enough for fast infinite-scroll over the full AMFI universe."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    scheme_code: str
    isin: Optional[str]
    scheme_name: str
    amc_name: str
    category: str
    sub_category: Optional[str]
    asset_class: Optional[str]
    asset_subgroup: Optional[str]
    risk_rating_sebi: Optional[str]
    returns_1y_pct: Optional[float]
    returns_3y_pct: Optional[float]
    returns_5y_pct: Optional[float]


class MfFundMetadataSearchResponse(BaseModel):
    items: List[MfFundMetadataListItem]
    total: int
    limit: int
    offset: int
    has_more: bool


class MfNavChartPoint(BaseModel):
    nav_date: date
    nav: float


class MfNavDerivedReturns(BaseModel):
    """Performance metrics computed from stored NAV rows (point-to-point / CAGR)."""

    return_1y_abs_pct: Optional[float] = None
    return_3y_cagr_pct: Optional[float] = None
    return_5y_cagr_pct: Optional[float] = None
    return_10y_cagr_pct: Optional[float] = None
    return_inception_abs_pct: Optional[float] = None
    return_inception_cagr_pct: Optional[float] = None
    first_nav_date: Optional[date] = None
    latest_nav: Optional[float] = None
    latest_nav_date: Optional[date] = None
    nav_row_count: int = 0


class MfMetadataReturnsSnapshot(BaseModel):
    """Broker-style headline numbers persisted on metadata (may lag NAV-derived figures)."""

    returns_1y_pct: Optional[float] = None
    returns_3y_pct: Optional[float] = None
    returns_5y_pct: Optional[float] = None
    returns_10y_pct: Optional[float] = None


class MfFundInvestorDetailResponse(BaseModel):
    """Fund facts + NAV-based performance for investor-focused UI (e.g. Groww-style detail)."""

    model_config = ConfigDict(from_attributes=True)

    metadata_id: uuid.UUID
    scheme_code: str
    scheme_name: str
    amc_name: str
    category: str
    sub_category: Optional[str]
    isin: Optional[str]
    isin_div_reinvest: Optional[str]
    plan_type: MfPlanType
    option_type: MfOptionType
    is_active: bool
    risk_rating_sebi: Optional[str]
    asset_class: Optional[str]
    asset_subgroup: Optional[str]
    direct_plan_fees: Optional[float]
    regular_plan_fees: Optional[float]
    exit_load_percent: Optional[float]
    exit_load_months: Optional[int]
    large_cap_equity_pct: Optional[float]
    mid_cap_equity_pct: Optional[float]
    small_cap_equity_pct: Optional[float]
    debt_pct: Optional[float]
    others_pct: Optional[float]
    returns_from_nav: MfNavDerivedReturns
    returns_from_metadata: MfMetadataReturnsSnapshot
    nav_chart: List[MfNavChartPoint]
    disclaimers: List[str] = Field(default_factory=list)
