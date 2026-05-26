"""Pydantic schemas for ``cashflow_input_one_off_events``."""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.cashflow.enums import OneOffDirection


class CashflowOneOffEventBase(BaseModel):
    direction: OneOffDirection
    description: str = Field(min_length=1)
    amount: float = Field(gt=0)
    event_date: date


class CashflowOneOffEventCreate(CashflowOneOffEventBase):
    pass


class CashflowOneOffEventUpdate(BaseModel):
    direction: OneOffDirection | None = None
    description: str | None = Field(default=None, min_length=1)
    amount: float | None = Field(default=None, gt=0)
    event_date: date | None = None


class CashflowOneOffEventResponse(CashflowOneOffEventBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
