"""Request/response schemas for the mfapi.in MF master + NAV ingestion endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.services.mf.mfapi_ingest_service import IngestMode


class MfapiRefreshRequest(BaseModel):
    mode: IngestMode = IngestMode.INCREMENTAL
    scheme_codes: Optional[List[str]] = None
    dry_run: bool = False
    #: Parallel fetches to mfapi.in; use ``1`` to process one scheme at a time (gentle on the API).
    concurrency: int = Field(default=1, ge=1, le=64)


class MfapiIngestResultSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    mode: str
    started_at: datetime
    finished_at: datetime
    schemes_seen: int = 0
    schemes_inserted: int = 0
    schemes_updated: int = 0
    nav_rows_inserted: int = 0
    nav_rows_candidate: int = 0
    nav_rows_skipped_duplicate: int = 0
    isin_collisions: int = 0
    parse_errors: int = 0
    failed_codes: List[str] = Field(default_factory=list)
    dry_run: bool = False


class BackfillIsinResultSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    aa_summaries_isin_filled: int = 0
    aa_transactions_isin_filled: int = 0
    nav_history_isin_filled: int = 0
    aa_summaries_scheme_filled: int = 0
    aa_transactions_scheme_filled: int = 0
