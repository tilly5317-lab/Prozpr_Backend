"""Pydantic schemas — `mfc_ft_schemas.py`.

Request/response shapes for the MF Central Financial Transaction API
(``/api/v1/mfc-ft/*``) — the OUTBOUND half of the MFC integration.

One request model covers all eight transaction families. That mirrors MFC's own
envelope, where the families differ only in which fields of one scheme object
they populate, and it keeps the per-family rules (a switch needs ``to_isin``, a
redemption needs one of amount/units/all) in the service where they are checked
once, rather than split across eight near-identical models that would each have
to re-state the shared 20.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

_PAN_RE = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")
_ISIN_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")


class MfcBankInput(BaseModel):
    """The account a redemption pays into, or a purchase debits.

    MFC requires it to be IMPS-verified or TPV'd on their side — we only pass
    it through, and cannot vouch for it.
    """

    account_no: str = Field(..., max_length=50)
    account_type: str = Field("SB", description="SB (savings) or CA (current).")
    name: Optional[str] = Field(None, max_length=50, description="Bank name.")
    branch: Optional[str] = Field(None, max_length=50)
    city: Optional[str] = Field(None, max_length=50)
    pincode: Optional[str] = Field(None, max_length=10)
    ifsc: Optional[str] = Field(None, max_length=20)
    neft_ifsc: Optional[str] = Field(None, max_length=20)
    micr: Optional[str] = Field(None, max_length=20)

    @field_validator("account_type")
    @classmethod
    def _account_type(cls, v: str) -> str:
        value = (v or "SB").strip().upper()
        if value not in {"SB", "CA"}:
            raise ValueError("Account type must be SB (savings) or CA (current).")
        return value


class MfcFtOrderRequest(BaseModel):
    """Place one financial transaction.

    ``kind`` decides which MFC endpoint is called and which of these fields are
    required — see the per-family rules in ``mfc_ft_service._validate``, which
    reject before anything reaches MFC so the error can say what is missing.
    """

    kind: str = Field(
        ...,
        description=(
            "purchase | additional | redeem | switch | stp | swp | "
            "sip_pause | sip_cancel"
        ),
    )

    # What to trade.
    isin: Optional[str] = Field(None, description="Scheme to buy/sell/switch OUT of.")
    to_isin: Optional[str] = Field(
        None, description="Scheme to switch/transfer INTO (switch, STP)."
    )
    folio: Optional[str] = Field(None, max_length=40)
    scheme_name: Optional[str] = Field(
        None,
        max_length=255,
        description="Used to infer the AMC when `amc` is not supplied.",
    )
    amc: Optional[str] = Field(
        None,
        max_length=40,
        description="MF Central AMC code, or a fund-house name we can resolve.",
    )

    amount: Optional[float] = Field(None, gt=0)
    units: Optional[float] = Field(None, gt=0)
    all_units: bool = Field(False, description="Redeem/switch the entire holding.")

    # SIP / STP / SWP.
    frequency: Optional[str] = Field(
        None, description="MFC code (OM, Q, W…) or a word we can resolve (Monthly)."
    )
    start_date: Optional[str] = Field(None, description="DD-MMM-YYYY.")
    end_date: Optional[str] = Field(None, description="DD-MMM-YYYY.")
    installments: Optional[int] = Field(None, ge=1)

    # Pause / cancel.
    user_trxn_no: Optional[str] = Field(
        None, max_length=64, description="Registrar's number for the running SIP."
    )
    pause_installments: Optional[int] = Field(None, ge=1)
    reason: Optional[str] = Field(None, max_length=200)
    reason_code: Optional[str] = Field(None, max_length=10)

    # Distribution. Direct plans leave these empty; MFC rejects a distributor id
    # on a direct plan and requires one on a regular plan.
    dist_id: Optional[str] = Field(None, max_length=20, description='e.g. "ARN-12345".')
    sub_broker_arn: Optional[str] = Field(None, max_length=20)
    euin: Optional[str] = Field(None, max_length=20)
    ria_code: Optional[str] = Field(None, max_length=20)
    reinvest: Optional[str] = Field(
        None, description="Z (reinvest) or Y (payout) — IDCW plans only."
    )

    bank: Optional[MfcBankInput] = None

    # Identity. Same rule as the CAS flow: the account's PAN wins when we hold
    # one, and the contact is the one registered with the FUND HOUSES.
    pan_no: Optional[str] = None
    mobile: Optional[str] = None
    email: Optional[str] = None

    @field_validator("isin", "to_isin")
    @classmethod
    def _isin_shape(cls, v: Optional[str]) -> Optional[str]:
        if v is None or not v.strip():
            return None
        value = v.strip().upper()
        if not _ISIN_RE.match(value):
            raise ValueError(f"'{v}' is not a valid ISIN.")
        return value

    @field_validator("pan_no")
    @classmethod
    def _pan_shape(cls, v: Optional[str]) -> Optional[str]:
        if v is None or not v.strip():
            return None
        value = v.strip().upper()
        if not _PAN_RE.match(value):
            raise ValueError("Enter a valid PAN (e.g. ABCDE1234F).")
        return value

    @field_validator("kind")
    @classmethod
    def _kind_known(cls, v: str) -> str:
        from app.domains.execution.models.mfc_ft_order import MFC_FT_KINDS

        value = (v or "").strip().lower()
        if value not in MFC_FT_KINDS:
            raise ValueError(f"Unknown transaction kind '{v}'.")
        return value


class MfcFtOtpRequest(BaseModel):
    """Relay the OTP the investor received."""

    otp: str = Field(..., min_length=4, max_length=10)


class MfcFtPaymentRequest(BaseModel):
    """Tell MFC the money moved. Purchases and SIP registrations only.

    Without it the RTA holds the order unfunded, so this is part of placing the
    order rather than bookkeeping after it.
    """

    status: str = Field("SUCCESS", description="SUCCESS or FAILURE.")
    bank_code: Optional[str] = Field(None, max_length=20)
    umrn: Optional[str] = Field(None, max_length=40, description="Mandate UMRN.")
    mandate_ref_id: Optional[str] = Field(None, max_length=40)
    error_description: Optional[str] = Field(None, max_length=500)


class MfcFtOrderItem(BaseModel):
    """One order, as the app sees it.

    Carries BOTH ``status`` (ours, what code branches on) and ``rta_status``
    (the registrar's own words, what a support desk recognises). They are not
    redundant: the registrars publish no closed list of their phrasings, so the
    mapping is lossy on purpose and the original is kept.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    kind: str
    status: str
    rta_status: Optional[str] = None
    client_ref_no: str
    req_id: Optional[str] = None
    user_trxn_no: Optional[str] = None
    amc: Optional[str] = None
    amc_name: Optional[str] = None
    folio: Optional[str] = None
    isin: Optional[str] = None
    to_isin: Optional[str] = None
    scheme_name: Optional[str] = None
    amount: Optional[float] = None
    units: Optional[float] = None
    all_units: Optional[bool] = None
    frequency: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    installments: Optional[int] = None
    otp_destination: Optional[str] = None
    error: Optional[str] = None
    created_at: datetime
    consented_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None


class MfcFtOrderResponse(BaseModel):
    """An order plus what the caller should do next."""

    order: MfcFtOrderItem
    message: str
    # Names the call the caller should make next, so a client does not have to
    # encode MFC's chain order itself.
    next_step: Optional[str] = Field(
        None, description="otp | consent | payment | status | none"
    )


class MfcFtOrderListResponse(BaseModel):
    orders: list[MfcFtOrderItem]


class MfcFtStatusResponse(BaseModel):
    """A polled order. ``raw`` is MFC's own body, kept so an unmapped registrar
    phrasing can be diagnosed without a redeploy."""

    order: MfcFtOrderItem
    raw: dict[str, Any] = Field(default_factory=dict)


class MfcFtValidateResponse(BaseModel):
    """Pre-flight result for a SIP pause/cancel. Nothing was written."""

    ok: bool
    raw: dict[str, Any] = Field(default_factory=dict)
    message: str


class MfcMastersResponse(BaseModel):
    """MFC's code tables, so a client can label and validate without hardcoding.

    Served rather than duplicated in the frontend because MFC extends them
    (a new AMC onboards) and a stale copy in a browser bundle would silently
    reject a real fund house.
    """

    amcs: list[dict[str, str]]
    frequencies: list[dict[str, str]]
    account_types: list[dict[str, str]]
    transaction_kinds: list[dict[str, str]]
