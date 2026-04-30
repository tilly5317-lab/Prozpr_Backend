from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Conversation + I/O
# ---------------------------------------------------------------------------


class ConversationTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class PortfolioQueryResponse(BaseModel):
    answer: Optional[str] = None
    guardrail_triggered: bool
    redirect_message: Optional[str] = None


# ---------------------------------------------------------------------------
# Client context
# ---------------------------------------------------------------------------


class ClientContext(BaseModel):
    age: int | None = None
    risk_category: str | None = None
    effective_risk_score: float | None = None
    investment_horizon: str | None = None
    occupation_type: str | None = None
    annual_income_inr: float | None = None
    total_liabilities_inr: float | None = None
    financial_goals: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Portfolio context
# ---------------------------------------------------------------------------


class Holding(BaseModel):
    name: str
    instrument_type: str | None = None
    asset_class: str | None = None
    sub_category: str | None = None
    quantity: float | None = None
    current_value_inr: float | None = None
    allocation_percentage: float | None = None
    return_1y_pct: float | None = None
    return_3y_pct: float | None = None


class AllocationRow(BaseModel):
    asset_class: str
    percentage: float
    amount_inr: float | None = None


class SubCategoryAllocationRow(BaseModel):
    asset_class: str | None = None
    sub_category: str
    percentage: float
    amount_inr: float | None = None


class PortfolioContext(BaseModel):
    total_value_inr: float | None = None
    total_invested_inr: float | None = None
    total_gain_percentage: float | None = None
    holdings: list[Holding] = Field(default_factory=list)
    allocations: list[AllocationRow] = Field(default_factory=list)
    sub_category_allocations: list[SubCategoryAllocationRow] = Field(default_factory=list)
