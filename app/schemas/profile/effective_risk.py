"""Pydantic schemas for EffectiveRiskAssessment read/recalculate."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel


class EffectiveRiskAssessmentResponse(BaseModel):
    model_config = {"from_attributes": True}

    step_name: str = "risk_profile"
    payload: dict[str, Any] = {}
    calculations: dict[str, Any] = {}
    output: dict[str, Any] = {}
    effective_risk_score: Optional[float] = None
    risk_capacity_score: Optional[float] = None
    risk_willingness: Optional[float] = None
    trigger_reason: Optional[str] = None
    computed_at: Optional[datetime] = None


class EffectiveRiskRecalculateResponse(BaseModel):
    effective_risk_score: Optional[float] = None
    risk_capacity_score: Optional[float] = None
    risk_willingness: Optional[float] = None
    recalculated: bool = True
