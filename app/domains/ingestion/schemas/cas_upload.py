"""Response shapes for a user's CAS upload history."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict


class CasUploadResponse(BaseModel):
    """One uploaded statement and the headline figures it produced.

    The figures are stored on the snapshot row itself, so listing a user's whole
    upload history costs one indexed query and never touches the ledger behind it.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    seq: int
    status: str
    cas_type: Optional[str] = None
    file_type: Optional[str] = None
    source_filename: Optional[str] = None
    statement_from: Optional[str] = None
    statement_to: Optional[str] = None
    folios: Optional[int] = None
    schemes: Optional[int] = None
    transactions: Optional[int] = None
    total_value_inr: Optional[float] = None
    total_invested_inr: Optional[float] = None
    cas_document_id: Optional[uuid.UUID] = None
    failure_reason: Optional[str] = None
    created_at: datetime
    activated_at: Optional[datetime] = None
    superseded_at: Optional[datetime] = None
