"""Pydantic schema — `effective_risk.py`.

Request/response or DTO shapes for API validation and OpenAPI documentation. Kept separate from ORM models so API contracts can evolve independently of database columns.
"""


from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


class EffectiveRiskAssessmentResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    step_name: str = "risk_profile"
    payload: dict[str, Any]
    calculations: dict[str, Any] = Field(default_factory=dict)
    output: dict[str, Any] = Field(default_factory=dict)
    effective_risk_score: Optional[float] = None
    risk_capacity_score: Optional[float] = None
    risk_willingness: Optional[float] = None
    trigger_reason: Optional[str] = None
    computed_at: Optional[datetime] = None


class EffectiveRiskRecalculateResponse(BaseModel):
    updated: bool
    assessment: Optional[EffectiveRiskAssessmentResponse] = None
    detail: Optional[str] = Field(
        default=None,
        description="Set when assessment could not be computed (e.g. missing date of birth).",
    )
