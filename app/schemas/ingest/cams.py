"""Pydantic schema — `cams.py`.

Response shape for the CAMS / KFintech Consolidated Account Statement (CAS) PDF
upload + ingest endpoint (``POST /api/v1/mf-ingest/cams-pdf``). The request side is
a multipart form (``file`` + ``password``), declared inline on the router.

Replaces the (sidelined) Finvu account-aggregator fetch-by-mobile flow.
"""

from __future__ import annotations

import uuid
from typing import Optional

from pydantic import BaseModel, Field


class CamsPdfImportResponse(BaseModel):
    import_id: uuid.UUID
    status: str  # MfAaImportStatus value after normalization (NORMALIZED / FAILED / RECEIVED)
    cas_file_type: Optional[str] = None  # casparser file_type, e.g. "CAMS" / "KARVY" / "CAMS_KARVY"
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
