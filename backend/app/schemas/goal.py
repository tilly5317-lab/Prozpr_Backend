"""Pydantic schema — `goal.py`.

Request/response or DTO shapes for API validation and OpenAPI documentation. Kept separate from ORM models so API contracts can evolve independently of database columns.
"""


from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator


class GoalCreate(BaseModel):
    """Accepts legacy frontend field names; stored as structured goals."""

    name: str = Field(..., min_length=1, max_length=100, description="Maps to goal_name")
    goal_type: Optional[str] = Field(default="OTHER", max_length=32)
    slug: Optional[str] = None
    icon: Optional[str] = None
    description: Optional[str] = None
    target_amount: float = Field(..., gt=0, description="Maps to present_value_amount (today's cost)")
    inflation_rate: Optional[float] = Field(default=None, ge=0, le=50)
    target_date: Optional[date] = None
    monthly_contribution: Optional[float] = None
    priority: str = Field(default="PRIMARY")
    notes: Optional[str] = None

    @field_validator("goal_type", mode="before")
    @classmethod
    def uppercase_goal_type(cls, v: Any) -> Any:
        if isinstance(v, str):
            return v.upper()
        return v

    @field_validator("priority", mode="before")
    @classmethod
    def normalize_priority(cls, v: Any) -> Any:
        if not isinstance(v, str):
            return "PRIMARY"
        x = v.lower()
        if x in ("low",):
            return "SECONDARY"
        if x in ("medium",):
            return "MEDIUM"
        if x in ("high", "primary"):
            return "PRIMARY"
        if x in ("secondary",):
            return "SECONDARY"
        u = v.upper()
        return u if u in ("PRIMARY", "SECONDARY", "MEDIUM") else "PRIMARY"


class GoalUpdate(BaseModel):
    name: Optional[str] = Field(None, max_length=100)
    goal_type: Optional[str] = None
    slug: Optional[str] = None
    icon: Optional[str] = None
    description: Optional[str] = None
    target_amount: Optional[float] = Field(None, gt=0)
    present_value_amount: Optional[float] = Field(None, gt=0)
    inflation_rate: Optional[float] = Field(None, ge=0, le=50)
    target_date: Optional[date] = None
    monthly_contribution: Optional[float] = None
    suggested_contribution: Optional[float] = None
    priority: Optional[str] = None
    status: Optional[str] = None
    notes: Optional[str] = None

    @field_validator("goal_type", "status", mode="before")
    @classmethod
    def uppercase_enums(cls, v: Any) -> Any:
        if isinstance(v, str):
            return v.upper()
        return v

    @field_validator("priority", mode="before")
    @classmethod
    def normalize_priority_update(cls, v: Any) -> Any:
        if v is None:
            return v
        if not isinstance(v, str):
            return v
        x = v.lower()
        if x == "low":
            return "SECONDARY"
        if x == "medium":
            return "MEDIUM"
        if x in ("high", "primary"):
            return "PRIMARY"
        if x == "secondary":
            return "SECONDARY"
        u = v.upper()
        return u if u in ("PRIMARY", "SECONDARY", "MEDIUM") else "PRIMARY"


class GoalResponse(BaseModel):
    """Stable JSON shape for existing clients (name, target_amount, etc.)."""

    id: uuid.UUID
    name: str
    slug: Optional[str] = None
    icon: Optional[str] = None
    description: Optional[str] = None
    target_amount: Optional[float] = None
    target_date: Optional[date] = None
    invested_amount: float = 0
    current_value: float = 0
    monthly_contribution: Optional[float] = None
    suggested_contribution: Optional[float] = None
    priority: str = "PRIMARY"
    status: str = "ACTIVE"
    goal_type: Optional[str] = None
    inflation_rate: Optional[float] = None
    notes: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class GoalDetailResponse(GoalResponse):
    contributions: list[GoalContributionResponse] = []
    holdings: list[GoalHoldingResponse] = []


class GoalContributionCreate(BaseModel):
    amount: float = Field(..., gt=0)
    note: Optional[str] = None


class GoalContributionResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    amount: float
    contributed_at: datetime
    note: Optional[str] = None


class GoalHoldingResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    fund_name: str
    category: Optional[str] = None
    invested_amount: float
    current_value: Optional[float] = None
    gain_percentage: Optional[float] = None


def goal_to_response(
    goal: Any,
    *,
    invested_amount: float = 0.0,
    current_value: float = 0.0,
) -> GoalResponse:
    return GoalResponse(
        id=goal.id,
        name=goal.goal_name,
        slug=None,
        icon=None,
        description=None,
        target_amount=float(goal.present_value_amount),
        target_date=goal.target_date,
        invested_amount=invested_amount,
        current_value=current_value,
        monthly_contribution=None,
        suggested_contribution=None,
        priority=goal.priority.value if hasattr(goal.priority, "value") else str(goal.priority),
        status=goal.status.value if hasattr(goal.status, "value") else str(goal.status),
        goal_type=goal.goal_type.value if hasattr(goal.goal_type, "value") else str(goal.goal_type),
        inflation_rate=float(goal.inflation_rate) if goal.inflation_rate is not None else None,
        notes=goal.notes,
        created_at=goal.created_at,
        updated_at=goal.updated_at,
    )
