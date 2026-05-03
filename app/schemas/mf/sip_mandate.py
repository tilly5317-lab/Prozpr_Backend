"""SIP mandate rows."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from app.models.mf.enums import MfSipFrequency, MfSipStatus, MfStepupFrequency


class MfSipMandateCreate(BaseModel):
    scheme_code: str = Field(..., max_length=20)
    folio_number: Optional[str] = Field(None, max_length=30)
    sip_amount: float = Field(..., gt=0)
    frequency: MfSipFrequency = MfSipFrequency.MONTHLY
    debit_day: int = Field(..., ge=1, le=28)
    start_date: date
    end_date: Optional[date] = None
    stepup_amount: Optional[float] = None
    stepup_percentage: Optional[float] = None
    stepup_frequency: Optional[MfStepupFrequency] = None
    status: MfSipStatus = MfSipStatus.ACTIVE


class MfSipMandateUpdate(BaseModel):
    scheme_code: Optional[str] = Field(None, max_length=20)
    folio_number: Optional[str] = Field(None, max_length=30)
    sip_amount: Optional[float] = Field(None, gt=0)
    frequency: Optional[MfSipFrequency] = None
    debit_day: Optional[int] = Field(None, ge=1, le=28)
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    stepup_amount: Optional[float] = None
    stepup_percentage: Optional[float] = None
    stepup_frequency: Optional[MfStepupFrequency] = None
    status: Optional[MfSipStatus] = None


class MfSipMandateResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    scheme_code: str
    folio_number: Optional[str]
    sip_amount: float
    frequency: MfSipFrequency
    debit_day: int
    start_date: date
    end_date: Optional[date]
    stepup_amount: Optional[float]
    stepup_percentage: Optional[float]
    stepup_frequency: Optional[MfStepupFrequency]
    status: MfSipStatus
    created_at: datetime
    updated_at: datetime
