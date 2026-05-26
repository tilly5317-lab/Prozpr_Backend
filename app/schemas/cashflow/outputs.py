"""Pydantic schemas for cashflow engine run outputs."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.cashflow.enums import DetailLevel, InvestmentSource


class CashflowPlanRunBase(BaseModel):
    engine_version: str
    cause: DetailLevel = DetailLevel.default
    assumption_id: uuid.UUID
    warnings: list[str] = Field(default_factory=list)
    computed_at: datetime


class CashflowPlanRunCreate(CashflowPlanRunBase):
    user_id: uuid.UUID | None = None
    chat_session_id: uuid.UUID | None = None


class CashflowPlanRunResponse(CashflowPlanRunBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID | None
    chat_session_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime


class AnnualCashflowRowSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    fy_end_date: date
    fy_label: str
    income: float
    income_tax: float
    household_expense: float
    savings_pre_emi: float
    existing_mortgage_emi: float
    goal_mortgage_emi: float
    savings_post_emi: float
    one_off_inflow: float = 0
    one_off_outflow: float = 0
    corpus_opening: float = 0
    monthly_investment: float = 0
    investment_returns: float = 0
    goal_payout: float = 0
    corpus_closing: float = 0
    is_funded: bool = True


class MonthlyCashflowRowSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    month_end_date: date
    fy_label: str
    income: float
    income_tax: float
    household_expense: float
    savings_pre_emi: float
    existing_mortgage_emi: float
    goal_mortgage_emi: float
    savings_post_emi: float
    one_off_inflow: float = 0
    one_off_outflow: float = 0
    corpus_opening: float = 0
    monthly_investment: float = 0
    investment_source: InvestmentSource = InvestmentSource.zero
    investment_returns: float = 0
    goal_payout: float = 0
    corpus_closing: float = 0
    is_funded: bool = True


class HeadlineStatusSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    years_to_last_goal: int
    last_goal_date: date
    last_fy_end_date: date
    number_of_goals: int
    corpus_today: float
    total_corpus_required_today: float
    surplus_or_shortfall_today: float
    corpus_closing: float
    total_shortfall_fv: float
    total_funded_amount: float


class FundFlowSummarySchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    corpus_opening: float
    total_investments: float
    total_roi: float
    total_one_off_in: float
    total_one_off_out: float
    total_goals_paid: float
    corpus_closing: float
    corpus_today: float
    total_corpus_required_today: float
    surplus_or_shortfall_today: float


class GoalBulletSchema(BaseModel):
    name: str
    verdict: str
    headline_amount: str
    note: str


class PlanSummarySchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    top_line: str
    retirement_note: str
    cashflow_note: str
    goals: list[GoalBulletSchema] | list[dict[str, Any]]
    risks: list[str]
    next_steps: list[str]
    summary_error: str | None = None


class CashflowPlanRunDetailResponse(CashflowPlanRunResponse):
    headline: HeadlineStatusSchema | None = None
    fund_flow_summary: FundFlowSummarySchema | None = None
    plan_summary: PlanSummarySchema | None = None
    annual_cashflow: list[AnnualCashflowRowSchema] = Field(default_factory=list)
    monthly_cashflow: list[MonthlyCashflowRowSchema] | None = None
