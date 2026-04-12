"""Pydantic schema — `investment.py`.

Request/response or DTO shapes for API validation and OpenAPI documentation. Kept separate from ORM models so API contracts can evolve independently of database columns.
"""


from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class InvestmentProfileUpdate(BaseModel):
    # Section 2 - Objectives
    objectives: Optional[list[str]] = None
    detailed_goals: Optional[list[dict]] = None
    portfolio_value: Optional[float] = None
    monthly_savings: Optional[float] = None
    target_corpus: Optional[float] = None
    target_timeline: Optional[str] = None
    annual_income: Optional[float] = None
    retirement_age: Optional[int] = None
    # Section 4 - Financial picture
    investable_assets: Optional[float] = None
    total_liabilities: Optional[float] = None
    property_value: Optional[float] = None
    mortgage_amount: Optional[float] = None
    annual_mortgage_payment: Optional[float] = None
    properties_owned: Optional[int] = None
    expected_inflows: Optional[float] = None
    regular_outgoings: Optional[float] = None
    planned_major_expenses: Optional[float] = None
    emergency_fund: Optional[float] = None
    emergency_fund_months: Optional[str] = None
    liquidity_needs: Optional[str] = None
    income_needs: Optional[float] = None
    # Section 6 - Time horizon
    is_multi_phase_horizon: Optional[bool] = None
    phase_description: Optional[str] = None
    total_horizon: Optional[str] = None


class InvestmentProfileResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    objectives: Optional[list[str]] = None
    detailed_goals: Optional[list[dict]] = None
    portfolio_value: Optional[float] = None
    monthly_savings: Optional[float] = None
    target_corpus: Optional[float] = None
    target_timeline: Optional[str] = None
    annual_income: Optional[float] = None
    retirement_age: Optional[int] = None
    investable_assets: Optional[float] = None
    total_liabilities: Optional[float] = None
    property_value: Optional[float] = None
    mortgage_amount: Optional[float] = None
    annual_mortgage_payment: Optional[float] = None
    properties_owned: Optional[int] = None
    expected_inflows: Optional[float] = None
    regular_outgoings: Optional[float] = None
    planned_major_expenses: Optional[float] = None
    emergency_fund: Optional[float] = None
    emergency_fund_months: Optional[str] = None
    liquidity_needs: Optional[str] = None
    income_needs: Optional[float] = None
    is_multi_phase_horizon: Optional[bool] = None
    phase_description: Optional[str] = None
    total_horizon: Optional[str] = None
    updated_at: Optional[datetime] = None
