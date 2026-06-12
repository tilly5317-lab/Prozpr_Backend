"""Pydantic schema — `issue_report.py`.

Request/response or DTO shapes for API validation and OpenAPI documentation. Kept separate from ORM models so API contracts can evolve independently of database columns.
"""


from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel


class IssueReportResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    source: str
    source_detail: str | None = None
    description: str
    has_screenshot: bool = False
    created_at: datetime
    message: str = "Issue reported. Our team will look into it."
