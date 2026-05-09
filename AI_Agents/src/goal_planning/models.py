"""Public Pydantic contracts for the goal_planning module.

All types here cross the engine↔agent boundary or are part of the public API
exported from goal_planning/__init__.py.
"""
from __future__ import annotations
from datetime import date, datetime
from enum import Enum
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, Field, model_validator


class Assumptions(BaseModel):
    inflation_property: float = 0.06
    inflation_child_abroad_education: float = 0.08
    inflation_child_local_education: float = 0.06
    inflation_child_marriage: float = 0.06
    inflation_household_expense: float = 0.06
    annual_income_growth: float = 0.08
    annual_invested_amount_growth: float = 0.08
    roi_near_term_post_tax: float = 0.05
    roi_mid_term_post_tax: float = 0.07
    roi_long_term_post_tax: float = 0.09
    roi_retired_portfolio_annual: float = 0.09
    near_term_horizon_years: int = 2
    medium_term_horizon_years: int = 3
    default_mortgage_tenure_years: int = 30
    default_mortgage_interest_annual: float = 0.075


class ClientProfile(BaseModel):
    latest_update_date: date
    annual_income: float
    tax_rate: float
    financial_assets: float
    financial_liabilities_excl_mortgage: float
    monthly_household_expense: float
    monthly_investment_next_12m: float | None = None


class RetirementInput(BaseModel):
    date_of_birth: date
    retirement_age: int = 60
    assumed_total_age: int = 85
    retirement_date_override: date | None = None
    retirement_corpus_pv_override: float | None = None
