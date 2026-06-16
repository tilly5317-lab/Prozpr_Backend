"""Pydantic schemas for unified ``goals`` (cashflow engine shape)."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domains.cashflow.schemas.enums import CashflowGoalType


class CashflowGoalBase(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    goal_type: CashflowGoalType
    goal_date: date

    goal_value_pv: float | None = Field(default=None, ge=0)
    goal_value_fv: float | None = Field(default=None, ge=0)
    inflation_rate: float | None = Field(default=None, ge=0.0, le=1.0)

    target_pv: float | None = Field(default=None, ge=0)
    target_fv: float | None = Field(default=None, ge=0)
    is_downpayment_only: bool = False
    upfront_amount: float | None = Field(default=None, ge=0)
    downpayment_pct: float | None = Field(default=None, ge=0.0, le=1.0)
    inflation_annual: float | None = Field(default=None, ge=0.0, le=1.0)
    mortgage_tenure_years: int | None = Field(default=None, gt=0)
    mortgage_interest_annual: float | None = Field(default=None, ge=0.0, le=1.0)

    date_of_birth: date | None = None

    @model_validator(mode="after")
    def _type_specific_fields(self) -> "CashflowGoalBase":
        if self.goal_type == CashflowGoalType.retirement:
            if self.date_of_birth is None:
                raise ValueError("date_of_birth is required for retirement goals")
        elif self.goal_type == CashflowGoalType.property:
            if self.target_pv is None and self.target_fv is None:
                raise ValueError("target_pv or target_fv required for property goals")
        elif self.goal_value_pv is None and self.goal_value_fv is None:
            raise ValueError(
                "goal_value_pv or goal_value_fv required for this goal type"
            )
        return self


class CashflowGoalCreate(CashflowGoalBase):
    pass


class CashflowGoalUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    goal_type: CashflowGoalType | None = None
    goal_date: date | None = None
    goal_value_pv: float | None = Field(default=None, ge=0)
    goal_value_fv: float | None = Field(default=None, ge=0)
    inflation_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    target_pv: float | None = Field(default=None, ge=0)
    target_fv: float | None = Field(default=None, ge=0)
    is_downpayment_only: bool | None = None
    upfront_amount: float | None = Field(default=None, ge=0)
    downpayment_pct: float | None = Field(default=None, ge=0.0, le=1.0)
    inflation_annual: float | None = Field(default=None, ge=0.0, le=1.0)
    mortgage_tenure_years: int | None = Field(default=None, gt=0)
    mortgage_interest_annual: float | None = Field(default=None, ge=0.0, le=1.0)
    date_of_birth: date | None = None


class CashflowGoalResponse(CashflowGoalBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    created_at: datetime
    updated_at: datetime

    # Legacy fields (optional on read during migration)
    goal_name: Optional[str] = None
    present_value_amount: Optional[float] = None
    target_date: Optional[date] = None
