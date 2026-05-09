"""Normalized MF ledger transactions."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from app.models.mf.enums import MfTransactionSource, MfTransactionType


class MfTransactionCreate(BaseModel):
    scheme_code: str = Field(..., max_length=20)
    sip_mandate_id: Optional[uuid.UUID] = None
    folio_number: str = Field(..., max_length=30)
    transaction_type: MfTransactionType
    transaction_date: date
    units: float
    nav: float = Field(..., gt=0)
    amount: float
    stamp_duty: Optional[float] = None
    source_system: MfTransactionSource = MfTransactionSource.MANUAL
    source_import_id: Optional[uuid.UUID] = None
    source_txn_fingerprint: Optional[str] = Field(None, max_length=128)


class MfTransactionUpdate(BaseModel):
    scheme_code: Optional[str] = Field(None, max_length=20)
    sip_mandate_id: Optional[uuid.UUID] = None
    folio_number: Optional[str] = Field(None, max_length=30)
    transaction_type: Optional[MfTransactionType] = None
    transaction_date: Optional[date] = None
    units: Optional[float] = None
    nav: Optional[float] = Field(None, gt=0)
    amount: Optional[float] = None
    stamp_duty: Optional[float] = None
    source_system: Optional[MfTransactionSource] = None
    source_import_id: Optional[uuid.UUID] = None
    source_txn_fingerprint: Optional[str] = Field(None, max_length=128)


class MfTransactionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    scheme_code: str
    sip_mandate_id: Optional[uuid.UUID]
    folio_number: str
    transaction_type: MfTransactionType
    transaction_date: date
    units: float
    nav: float
    amount: float
    stamp_duty: Optional[float]
    source_system: MfTransactionSource
    source_import_id: Optional[uuid.UUID]
    source_txn_fingerprint: Optional[str]
    created_at: datetime
