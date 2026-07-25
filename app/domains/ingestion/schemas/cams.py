"""Pydantic schema — `cams.py`.

Response shape for the CAMS / KFintech Consolidated Account Statement (CAS) PDF
upload + ingest endpoint (``POST /api/v1/mf-ingest/cams-pdf``). The request side is
a multipart form (``file`` + ``password``), declared inline on the router.

Replaces the (sidelined) Finvu account-aggregator fetch-by-mobile flow.
"""

from __future__ import annotations

import re
import uuid
from typing import Optional

from pydantic import BaseModel, Field, field_validator

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$")
_PAN_RE = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")


class CamsStatementRequestBody(BaseModel):
    """Ask CAMS/KFintech (via the CAS Parser API) to EMAIL the investor a
    detailed CAS PDF protected with a password of their choosing."""

    email: str = Field(..., description="Email registered with the mutual funds.")
    password: str = Field(
        ...,
        min_length=8,
        max_length=64,
        description="Password the CAS PDF will be encrypted with (user-chosen).",
    )
    pan_no: Optional[str] = Field(
        None, description="Investor PAN — optional, narrows the statement."
    )

    @field_validator("email")
    @classmethod
    def _email_shape(cls, v: str) -> str:
        v = v.strip().lower()
        if not _EMAIL_RE.match(v):
            raise ValueError("Enter a valid email address.")
        return v

    @field_validator("password")
    @classmethod
    def _password_rules(cls, v: str) -> str:
        # CAMS mailback rejects weak passwords — mirror the frontend checklist.
        if not (
            re.search(r"[A-Za-z]", v)
            and re.search(r"\d", v)
            and re.search(r"[^A-Za-z0-9\s]", v)
        ):
            raise ValueError(
                "Password must contain a letter, a number and a special character."
            )
        if re.search(r"\s", v):
            raise ValueError("Password cannot contain spaces.")
        return v

    @field_validator("pan_no")
    @classmethod
    def _pan_shape(cls, v: Optional[str]) -> Optional[str]:
        if v is None or not v.strip():
            return None
        v = v.strip().upper()
        if not _PAN_RE.match(v):
            raise ValueError("Enter a valid PAN (e.g. ABCDE1234F).")
        return v


class CamsCapabilitiesResponse(BaseModel):
    """Which halves of the CAMS import flow the configured CAS Parser API key
    supports — derived from the key's enabled_features, so the frontend can
    hide the statement-by-email step on plans without the generator."""

    parse_available: bool
    mailback_available: bool


class CamsStatementRequestResponse(BaseModel):
    status: str  # "success" — the registrar accepted the mailback request
    email: str  # where the statement will arrive
    message: str


class CamsPdfImportResponse(BaseModel):
    import_id: uuid.UUID
    status: str  # MfAaImportStatus value after normalization (NORMALIZED / FAILED / RECEIVED)
    cas_file_type: Optional[str] = (
        None  # casparser file_type, e.g. "CAMS" / "KARVY" / "CAMS_KARVY"
    )
    cas_type: Optional[str] = None  # "DETAILED" (with transactions) or "SUMMARY"
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
    # Blank identity fields on the user's account that were populated from the CAS
    # investor block (e.g. ["first_name", "last_name", "email", "address", "pan"]).
    profile_fields_filled: list[str] = Field(default_factory=list)
    message: str
