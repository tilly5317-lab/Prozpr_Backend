"""Account-aggregator import batch + child summary/transaction rows."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from app.models.mf.enums import MfAaImportStatus


class MfAaImportCreate(BaseModel):
    user_id: Optional[uuid.UUID] = None
    pan: Optional[str] = Field(None, max_length=20)
    pekrn: Optional[str] = Field(None, max_length=32)
    email: Optional[str] = Field(None, max_length=320)
    mobile: Optional[str] = Field(None, max_length=20)
    from_date: Optional[str] = Field(None, max_length=20)
    to_date: Optional[str] = Field(None, max_length=20)
    req_id: Optional[str] = Field(None, max_length=64)
    investor_first_name: Optional[str] = Field(None, max_length=100)
    investor_middle_name: Optional[str] = Field(None, max_length=100)
    investor_last_name: Optional[str] = Field(None, max_length=100)
    address_line_1: Optional[str] = Field(None, max_length=255)
    address_line_2: Optional[str] = Field(None, max_length=255)
    address_line_3: Optional[str] = Field(None, max_length=255)
    city: Optional[str] = Field(None, max_length=100)
    district: Optional[str] = Field(None, max_length=100)
    state: Optional[str] = Field(None, max_length=100)
    pincode: Optional[str] = Field(None, max_length=20)
    country: Optional[str] = Field(None, max_length=100)
    source_file: Optional[str] = Field(None, max_length=255)
    status: MfAaImportStatus = MfAaImportStatus.RECEIVED
    normalized_at: Optional[datetime] = None
    failure_reason: Optional[str] = Field(None, max_length=255)


class MfAaImportUpdate(BaseModel):
    user_id: Optional[uuid.UUID] = None
    pan: Optional[str] = Field(None, max_length=20)
    pekrn: Optional[str] = Field(None, max_length=32)
    email: Optional[str] = Field(None, max_length=320)
    mobile: Optional[str] = Field(None, max_length=20)
    from_date: Optional[str] = Field(None, max_length=20)
    to_date: Optional[str] = Field(None, max_length=20)
    req_id: Optional[str] = Field(None, max_length=64)
    investor_first_name: Optional[str] = Field(None, max_length=100)
    investor_middle_name: Optional[str] = Field(None, max_length=100)
    investor_last_name: Optional[str] = Field(None, max_length=100)
    address_line_1: Optional[str] = Field(None, max_length=255)
    address_line_2: Optional[str] = Field(None, max_length=255)
    address_line_3: Optional[str] = Field(None, max_length=255)
    city: Optional[str] = Field(None, max_length=100)
    district: Optional[str] = Field(None, max_length=100)
    state: Optional[str] = Field(None, max_length=100)
    pincode: Optional[str] = Field(None, max_length=20)
    country: Optional[str] = Field(None, max_length=100)
    source_file: Optional[str] = Field(None, max_length=255)
    status: Optional[MfAaImportStatus] = None
    normalized_at: Optional[datetime] = None
    failure_reason: Optional[str] = Field(None, max_length=255)


class MfAaImportResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: Optional[uuid.UUID]
    pan: Optional[str]
    pekrn: Optional[str]
    email: Optional[str]
    mobile: Optional[str]
    from_date: Optional[str]
    to_date: Optional[str]
    req_id: Optional[str]
    investor_first_name: Optional[str]
    investor_middle_name: Optional[str]
    investor_last_name: Optional[str]
    address_line_1: Optional[str]
    address_line_2: Optional[str]
    address_line_3: Optional[str]
    city: Optional[str]
    district: Optional[str]
    state: Optional[str]
    pincode: Optional[str]
    country: Optional[str]
    source_file: Optional[str]
    status: MfAaImportStatus
    normalized_at: Optional[datetime]
    failure_reason: Optional[str]
    imported_at: datetime
    created_at: datetime


class MfAaSummaryCreate(BaseModel):
    row_no: int = 0
    amc: Optional[str] = Field(None, max_length=20)
    amc_name: Optional[str] = Field(None, max_length=200)
    asset_type: Optional[str] = Field(None, max_length=30)
    broker_code: Optional[str] = Field(None, max_length=50)
    broker_name: Optional[str] = Field(None, max_length=200)
    closing_balance: Optional[float] = None
    cost_value: Optional[float] = None
    decimal_amount: Optional[int] = None
    decimal_nav: Optional[int] = None
    decimal_units: Optional[int] = None
    folio: Optional[str] = Field(None, max_length=40)
    is_demat: Optional[str] = Field(None, max_length=5)
    isin: Optional[str] = Field(None, max_length=20)
    kyc_status: Optional[str] = Field(None, max_length=20)
    last_nav_date: Optional[str] = Field(None, max_length=20)
    last_trxn_date: Optional[str] = Field(None, max_length=20)
    market_value: Optional[float] = None
    nav: Optional[float] = None
    nominee_status: Optional[str] = Field(None, max_length=20)
    opening_bal: Optional[float] = None
    rta_code: Optional[str] = Field(None, max_length=30)
    scheme: Optional[str] = Field(None, max_length=20)
    scheme_name: Optional[str] = Field(None, max_length=255)
    tax_status: Optional[str] = Field(None, max_length=20)


class MfAaSummaryUpdate(BaseModel):
    row_no: Optional[int] = None
    amc: Optional[str] = Field(None, max_length=20)
    amc_name: Optional[str] = Field(None, max_length=200)
    asset_type: Optional[str] = Field(None, max_length=30)
    broker_code: Optional[str] = Field(None, max_length=50)
    broker_name: Optional[str] = Field(None, max_length=200)
    closing_balance: Optional[float] = None
    cost_value: Optional[float] = None
    decimal_amount: Optional[int] = None
    decimal_nav: Optional[int] = None
    decimal_units: Optional[int] = None
    folio: Optional[str] = Field(None, max_length=40)
    is_demat: Optional[str] = Field(None, max_length=5)
    isin: Optional[str] = Field(None, max_length=20)
    kyc_status: Optional[str] = Field(None, max_length=20)
    last_nav_date: Optional[str] = Field(None, max_length=20)
    last_trxn_date: Optional[str] = Field(None, max_length=20)
    market_value: Optional[float] = None
    nav: Optional[float] = None
    nominee_status: Optional[str] = Field(None, max_length=20)
    opening_bal: Optional[float] = None
    rta_code: Optional[str] = Field(None, max_length=30)
    scheme: Optional[str] = Field(None, max_length=20)
    scheme_name: Optional[str] = Field(None, max_length=255)
    tax_status: Optional[str] = Field(None, max_length=20)


class MfAaSummaryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    aa_import_id: uuid.UUID
    row_no: int
    amc: Optional[str]
    amc_name: Optional[str]
    asset_type: Optional[str]
    broker_code: Optional[str]
    broker_name: Optional[str]
    closing_balance: Optional[float]
    cost_value: Optional[float]
    decimal_amount: Optional[int]
    decimal_nav: Optional[int]
    decimal_units: Optional[int]
    folio: Optional[str]
    is_demat: Optional[str]
    isin: Optional[str]
    kyc_status: Optional[str]
    last_nav_date: Optional[str]
    last_trxn_date: Optional[str]
    market_value: Optional[float]
    nav: Optional[float]
    nominee_status: Optional[str]
    opening_bal: Optional[float]
    rta_code: Optional[str]
    scheme: Optional[str]
    scheme_name: Optional[str]
    tax_status: Optional[str]
    created_at: datetime


class MfAaTransactionCreate(BaseModel):
    row_no: int = 0
    amc: Optional[str] = Field(None, max_length=20)
    amc_name: Optional[str] = Field(None, max_length=200)
    check_digit: Optional[str] = Field(None, max_length=10)
    folio: Optional[str] = Field(None, max_length=40)
    isin: Optional[str] = Field(None, max_length=20)
    posted_date: Optional[str] = Field(None, max_length=20)
    purchase_price: Optional[float] = None
    scheme: Optional[str] = Field(None, max_length=20)
    scheme_name: Optional[str] = Field(None, max_length=255)
    stamp_duty: Optional[float] = None
    stt_tax: Optional[float] = None
    tax: Optional[float] = None
    total_tax: Optional[float] = None
    trxn_amount: Optional[float] = None
    trxn_charge: Optional[float] = None
    trxn_date: Optional[str] = Field(None, max_length=20)
    trxn_desc: Optional[str] = Field(None, max_length=100)
    trxn_mode: Optional[str] = Field(None, max_length=10)
    trxn_type_flag: Optional[str] = Field(None, max_length=20)
    trxn_units: Optional[float] = None


class MfAaTransactionUpdate(BaseModel):
    row_no: Optional[int] = None
    amc: Optional[str] = Field(None, max_length=20)
    amc_name: Optional[str] = Field(None, max_length=200)
    check_digit: Optional[str] = Field(None, max_length=10)
    folio: Optional[str] = Field(None, max_length=40)
    isin: Optional[str] = Field(None, max_length=20)
    posted_date: Optional[str] = Field(None, max_length=20)
    purchase_price: Optional[float] = None
    scheme: Optional[str] = Field(None, max_length=20)
    scheme_name: Optional[str] = Field(None, max_length=255)
    stamp_duty: Optional[float] = None
    stt_tax: Optional[float] = None
    tax: Optional[float] = None
    total_tax: Optional[float] = None
    trxn_amount: Optional[float] = None
    trxn_charge: Optional[float] = None
    trxn_date: Optional[str] = Field(None, max_length=20)
    trxn_desc: Optional[str] = Field(None, max_length=100)
    trxn_mode: Optional[str] = Field(None, max_length=10)
    trxn_type_flag: Optional[str] = Field(None, max_length=20)
    trxn_units: Optional[float] = None


class MfAaTransactionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    aa_import_id: uuid.UUID
    row_no: int
    amc: Optional[str]
    amc_name: Optional[str]
    check_digit: Optional[str]
    folio: Optional[str]
    isin: Optional[str]
    posted_date: Optional[str]
    purchase_price: Optional[float]
    scheme: Optional[str]
    scheme_name: Optional[str]
    stamp_duty: Optional[float]
    stt_tax: Optional[float]
    tax: Optional[float]
    total_tax: Optional[float]
    trxn_amount: Optional[float]
    trxn_charge: Optional[float]
    trxn_date: Optional[str]
    trxn_desc: Optional[str]
    trxn_mode: Optional[str]
    trxn_type_flag: Optional[str]
    trxn_units: Optional[float]
    created_at: datetime
