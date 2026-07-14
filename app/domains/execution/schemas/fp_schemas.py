"""Request/response schemas for FP order execution."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field


class FpSetupRequest(BaseModel):
    """One-time account setup. PAN's 4th letter must be 'P' (individual) — the
    sandbox test PAN is ARRPP7775N. Email/mobile default from the user; the
    address defaults to a sandbox-safe constant."""

    name: str = Field(max_length=255)
    pan: str = Field(min_length=10, max_length=10)
    date_of_birth: str = Field(description="YYYY-MM-DD")
    bank_account_number: Optional[str] = Field(default=None, max_length=30)
    bank_ifsc: Optional[str] = Field(default=None, max_length=15)
    email: Optional[str] = Field(default=None, max_length=320)
    mobile: Optional[str] = Field(default=None, max_length=20)


class FpKycRequest(BaseModel):
    """Run a KYC readiness check (Pre-Verification) on PAN + name + DOB."""

    name: str = Field(max_length=255)
    pan: str = Field(min_length=10, max_length=10)
    date_of_birth: str = Field(description="YYYY-MM-DD")


class FpKycResponse(BaseModel):
    """Summarised pre_verification result. ``status`` is FP's job state
    (accepted -> completed); per-field statuses are verified/failed once done."""

    id: str
    status: str
    verified: bool
    pan_status: Optional[str] = None
    pan_code: Optional[str] = None
    name_status: Optional[str] = None
    dob_status: Optional[str] = None
    readiness_status: Optional[str] = None
    readiness_code: Optional[str] = None


class FpKycSetupRequest(BaseModel):
    """The KYC page's single call: PAN from the user; name / DOB / email /
    mobile come from the user's identity on our backend. PAN is optional on
    re-poll (the stored PAN is reused)."""

    pan: Optional[str] = Field(default=None, min_length=10, max_length=10)


class FpAccountResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    fp_investor_id: Optional[str] = None
    fp_investment_account_id: Optional[str] = None
    pan: Optional[str] = None
    holder_name: Optional[str] = None
    bank_account_masked: Optional[str] = None
    kyc_status: str
    kyc_pv_id: Optional[str] = None
    kyc_checked_at: Optional[datetime] = None
    created_at: datetime


class FpStatusResponse(BaseModel):
    configured: bool
    account: Optional[FpAccountResponse] = None
    kyc_complete: bool = False
    ready_to_transact: bool = False


class FpKycSetupResponse(BaseModel):
    """Combined result of the KYC page submit: the Pre-Verification summary
    plus the account row (setup runs automatically once KYC completes)."""

    kyc: Optional[FpKycResponse] = None
    account: FpAccountResponse
    ready_to_transact: bool = False


class FpLumpsumRequest(BaseModel):
    scheme_code: str = Field(max_length=40, description="AMFI code or ISIN")
    amount: float = Field(gt=0)


class FpSipRequest(BaseModel):
    scheme_code: str = Field(max_length=40, description="AMFI code or ISIN")
    amount: float = Field(gt=0)
    installment_day: int = Field(default=5, ge=1, le=28)
    number_of_installments: int = Field(default=12, ge=1, le=1200)


class FpOrderResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    kind: str
    state: str
    scheme_code: Optional[str] = None
    isin: Optional[str] = None
    scheme_name: Optional[str] = None
    amount: float
    installment_day: Optional[int] = None
    number_of_installments: Optional[int] = None
    fp_id: str
    created_at: datetime


class FpRebalanceExecuteRequest(BaseModel):
    """Optional per-trade overrides from the order-review UI: trade id ->
    amount (INR). A trade set to 0 (or negative) is skipped."""

    amounts: Optional[dict[str, float]] = None


class FpOrderBatchResponse(BaseModel):
    """Result of a batch placement (SIP plan / rebalance buys)."""

    orders: List[FpOrderResponse]
    failed: List[str] = []
