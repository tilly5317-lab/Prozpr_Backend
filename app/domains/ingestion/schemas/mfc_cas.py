"""Pydantic schemas — `mfc_cas.py`.

Request/response shapes for the MF Central CAS consent flow
(``/api/v1/mfc-cas/*``). The import response deliberately carries TWO views of
the same statement:

* ``ingest`` — what the pipeline actually stored (schemes, transactions,
  portfolio value), identical in shape to the PDF upload's response so the
  frontend's success handling is shared;
* ``data`` — everything MFC returned, flattened. MFC sends materially more than
  our schema stores (bank mandate, transactability flags, KYC/nominee status,
  demat split), and this is what the import screen renders.

Keeping them separate keeps the second from looking like state we hold.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

_PAN_RE = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$")


class MfcConfigResponse(BaseModel):
    """Whether the flow is usable on this server, and how it should be presented.

    ``integration_mode`` tells the frontend which of MFC's three delivery modes
    to use for the consent step. It is a server setting rather than a frontend
    constant because the correct answer depends on the deployment's origin,
    which the frontend does not reliably know inside a WebView.
    """

    enabled: bool
    environment: str = Field(
        description="uat | production, inferred from the base URL."
    )
    redirect_url: Optional[str] = Field(
        default=None,
        description="Where MFC returns the investor after the QR download.",
    )
    mfc_origin: Optional[str] = Field(
        default=None,
        description="Origin of MFC's consent UI — validate postMessage against this.",
    )
    integration_mode: str = Field(
        default="popup", description="popup | iframe | redirect"
    )


class MfcStartRequest(BaseModel):
    """Begin a consent request.

    Every field is optional: the server prefers the authenticated user's own
    PAN, mobile and email, and a supplied PAN is only honoured when we hold
    none. Supplying a mobile/email that differs from the account's is legitimate
    — it is the contact registered with the FUND HOUSES, which routinely differs
    from the Prozpr login.
    """

    pan_no: Optional[str] = Field(
        default=None, description="Only used when the account has no PAN on file."
    )
    mobile: Optional[str] = Field(
        default=None, description="Mobile registered with the mutual funds."
    )
    email: Optional[str] = Field(
        default=None, description="Email registered with the mutual funds."
    )
    to_date: Optional[str] = Field(
        default=None, description="DD-MMM-YYYY; defaults to today."
    )

    @field_validator("pan_no")
    @classmethod
    def _pan_shape(cls, v: Optional[str]) -> Optional[str]:
        if v is None or not v.strip():
            return None
        value = v.strip().upper()
        if not _PAN_RE.match(value):
            raise ValueError("Enter a valid PAN (e.g. ABCDE1234F).")
        return value

    @field_validator("email")
    @classmethod
    def _email_shape(cls, v: Optional[str]) -> Optional[str]:
        if v is None or not v.strip():
            return None
        value = v.strip().lower()
        if not _EMAIL_RE.match(value):
            raise ValueError("Enter a valid email address.")
        return value

    @field_validator("mobile")
    @classmethod
    def _mobile_shape(cls, v: Optional[str]) -> Optional[str]:
        if v is None or not v.strip():
            return None
        value = v.strip()
        digits = re.sub(r"\D", "", value)
        if len(digits) < 10:
            raise ValueError("Enter a 10-digit mobile number.")
        return value


class MfcStartResponse(BaseModel):
    """The redirect URL plus the identifiers needed to redeem the QR later."""

    request_id: uuid.UUID
    client_ref_no: str
    req_id: str
    otp_ref: str
    redirect_url: str
    pan_masked: str
    from_date: str
    to_date: str
    message: str


class MfcValidateQrRequest(BaseModel):
    """Redeem the QR the investor downloaded from MFC.

    ``qr_code`` is the PNG as base64; a full ``data:`` URL is accepted and
    stripped server-side, because native WebView bridges send that form.
    """

    qr_code: str = Field(..., description="Base64 PNG of the QR MFC produced.")
    request_id: Optional[uuid.UUID] = Field(
        default=None, description="Our request id from /mfc-cas/start."
    )
    req_id: Optional[str] = Field(
        default=None, description="MFC's request id, if the frontend lost ours."
    )


class MfcIngestSummary(BaseModel):
    """What the pipeline stored — mirrors the CAMS PDF upload response."""

    import_id: uuid.UUID
    cas_upload_id: Optional[uuid.UUID] = None
    status: str
    cas_type: Optional[str] = None
    statement_period_from: Optional[str] = None
    statement_period_to: Optional[str] = None
    folios: int
    schemes: int
    aa_transactions_parsed: int
    mf_transactions_inserted: int
    mf_transactions_skipped_duplicate: int
    portfolio_allocation_rows: int
    total_value_inr: float
    normalize_error: Optional[str] = None
    profile_fields_filled: list[str] = Field(default_factory=list)
    reused_existing: bool = False


class MfcImportResponse(BaseModel):
    """Result of exchanging one QR.

    ``ingest`` is null when the statement was readable but not ingestible — a
    Summary CAS, or a portfolio worth nothing. ``rejection`` then carries the
    reason, and ``data`` is still populated so the screen can show the investor
    exactly what MFC returned instead of an empty error page.
    """

    request_id: uuid.UUID
    req_id: str
    variant: str = Field(description="summary | detailed — chosen by the investor.")
    ingest: Optional[MfcIngestSummary] = None
    rejection: Optional[str] = None
    data: dict[str, Any] = Field(
        default_factory=dict, description="Everything MFC returned, flattened."
    )
    message: str


class MfcRequestItem(BaseModel):
    """One row of the consent-attempt history."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    client_ref_no: str
    req_id: Optional[str] = None
    status: str
    cas_variant: Optional[str] = None
    pan: Optional[str] = None
    mobile: Optional[str] = None
    email: Optional[str] = None
    from_date: Optional[str] = None
    to_date: Optional[str] = None
    folios: Optional[int] = None
    schemes: Optional[int] = None
    transactions: Optional[int] = None
    total_value_inr: Optional[float] = None
    error: Optional[str] = None
    created_at: datetime
    completed_at: Optional[datetime] = None


class MfcRequestListResponse(BaseModel):
    requests: list[MfcRequestItem]
