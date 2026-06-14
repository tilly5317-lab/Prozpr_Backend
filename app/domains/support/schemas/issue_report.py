"""Pydantic schema — `issue_report.py`.

Request/response or DTO shapes for API validation and OpenAPI documentation. The support
domain has NO database model by design — reports are appended to an Excel log and emailed.
"""


from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel


class IssueReportResponse(BaseModel):
    id: uuid.UUID
    source: str
    source_detail: str | None = None
    description: str
    has_screenshot: bool = False
    created_at: datetime
    message: str = "Issue reported. Our team will look into it."
