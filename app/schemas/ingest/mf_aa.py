"""Pydantic schema — `mf_aa.py`.

Request/response or DTO shapes for API validation and OpenAPI documentation. Kept separate from ORM models so API contracts can evolve independently of database columns.
"""


from __future__ import annotations

import uuid
from typing import Optional

from pydantic import BaseModel, Field


class MfAaNormalizeOneResponse(BaseModel):
    import_id: uuid.UUID
    status: str
    inserted: int
    skipped_duplicate: int
    error: Optional[str] = None


class MfAaNormalizePendingRequest(BaseModel):
    limit: int = Field(default=25, ge=1, le=500)


class MfAaNormalizePendingResponse(BaseModel):
    total_imports: int
    total_inserted: int
    total_skipped_duplicate: int
    results: list[MfAaNormalizeOneResponse]
