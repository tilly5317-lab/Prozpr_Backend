"""Request/response schemas for the DPDP rights endpoints."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.domains.privacy.models.consent import ConsentPurpose


class PurposeNotice(BaseModel):
    purpose: ConsentPurpose
    title: str
    detail: str
    necessary: bool
    granted: Optional[bool] = Field(
        default=None,
        description="Current position. Null means never asked — which is NOT consent.",
    )
    recorded_at: Optional[datetime] = None
    policy_version: Optional[str] = None


class PrivacyNoticeResponse(BaseModel):
    policy_version: str
    content_hash: str
    purposes: list[PurposeNotice]
    grievance_contact: Optional[str] = None


class ConsentUpdateItem(BaseModel):
    purpose: ConsentPurpose
    granted: bool


class ConsentUpdateRequest(BaseModel):
    """Withdrawal is the same call with ``granted: false`` — the Act requires it
    to be as easy as granting, so it is deliberately not a separate endpoint
    with a different shape."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {"consents": [{"purpose": "marketing_comms", "granted": False}]}
            ]
        }
    )

    consents: list[ConsentUpdateItem] = Field(..., min_length=1)


class ConsentStateResponse(BaseModel):
    policy_version: str
    purposes: list[PurposeNotice]


class GrievanceRequest(BaseModel):
    category: str = Field(
        default="general",
        description="One of: access, correction, erasure, consent, general.",
    )
    message: str = Field(..., min_length=10, max_length=4000)


class GrievanceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    category: str
    status: str
    created_at: datetime


class ErasureResponse(BaseModel):
    """Deliberately explicit about what has and has not happened yet — an
    erasure confirmation that overstates itself is its own compliance problem."""

    deleted_at: datetime
    purge_scheduled_for: datetime
    grace_days: int
    detail: str


class DataExportResponse(BaseModel):
    generated_at: str
    policy_version: str
    about: str
    purposes: dict[str, Any]
    recipients: list[dict[str, str]]
    statement_archive: list[dict[str, Any]]
    truncated_tables: list[str]
    row_cap_per_table: int
    tables: dict[str, Any]
