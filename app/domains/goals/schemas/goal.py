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

    name: str = Field(
        ..., min_length=1, max_length=100, description="Maps to goal_name"
    )
    goal_type: Optional[str] = Field(default="OTHER", max_length=32)
    slug: Optional[str] = None
    icon: Optional[str] = None
    description: Optional[str] = None
    target_amount: float = Field(
        ..., gt=0, description="Maps to present_value_amount (today's cost)"
    )
    inflation_rate: Optional[float] = Field(default=None, ge=0, le=50)
    target_date: Optional[date] = None
    monthly_contribution: Optional[float] = None
    priority: str = Field(default="HIGH")
    notes: Optional[str] = None
    # Asset-allocation pipeline fields (folded onto the canonical goal row).
    time_to_goal_months: Optional[int] = Field(default=None, ge=0)
    amount_needed: Optional[float] = Field(default=None, gt=0)
    goal_priority: Optional[str] = None  # "negotiable" | "non_negotiable"
    investment_goal: Optional[str] = None  # "wealth_creation" | "safety" | ...

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
            return "HIGH"
        x = v.lower()
        if x in ("low",):
            return "LOW"
        if x in ("medium",):
            return "MEDIUM"
        if x in ("high", "primary"):
            return "HIGH"
        if x in ("secondary",):
            return "LOW"
        u = v.upper()
        if u in ("HIGH", "MEDIUM", "LOW"):
            return u
        if u == "PRIMARY":
            return "HIGH"
        if u == "SECONDARY":
            return "LOW"
        return "HIGH"


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
    time_to_goal_months: Optional[int] = Field(None, ge=0)
    amount_needed: Optional[float] = Field(None, gt=0)
    goal_priority: Optional[str] = None
    investment_goal: Optional[str] = None

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
            return "LOW"
        if x == "medium":
            return "MEDIUM"
        if x in ("high", "primary"):
            return "HIGH"
        if x == "secondary":
            return "LOW"
        u = v.upper()
        if u in ("HIGH", "MEDIUM", "LOW"):
            return u
        if u == "PRIMARY":
            return "HIGH"
        if u == "SECONDARY":
            return "LOW"
        return "HIGH"


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
    priority: str = "HIGH"
    status: str = "ACTIVE"
    goal_type: Optional[str] = None
    inflation_rate: Optional[float] = None
    notes: Optional[str] = None
    time_to_goal_months: Optional[int] = None
    amount_needed: Optional[float] = None
    goal_priority: Optional[str] = None
    investment_goal: Optional[str] = None
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


def _first_float(goal: Any, *attrs: str) -> float | None:
    """Read the first non-null numeric column (legacy + cashflow shapes)."""
    for attr in attrs:
        raw = getattr(goal, attr, None)
        if raw is not None:
            return float(raw)
    return None


def _goal_display_name(goal: Any) -> str:
    for attr in ("name", "goal_name"):
        raw = getattr(goal, attr, None)
        if isinstance(raw, str) and raw.strip():
            return raw.strip()[:100]
    return "Unnamed goal"


def _goal_target_date(goal: Any) -> date | None:
    return getattr(goal, "target_date", None) or getattr(goal, "goal_date", None)


def _enum_value(value: Any, default: str) -> str:
    if value is None:
        return default
    return value.value if hasattr(value, "value") else str(value)


def goal_to_response(
    goal: Any,
    *,
    invested_amount: float = 0.0,
    current_value: float = 0.0,
) -> GoalResponse:
    gt = getattr(goal, "goal_type", None)
    goal_type_str = (
        gt.value
        if gt is not None and hasattr(gt, "value")
        else (str(gt) if gt else None)
    )

    return GoalResponse(
        id=goal.id,
        name=_goal_display_name(goal),
        slug=None,
        icon=None,
        description=None,
        target_amount=_first_float(
            goal,
            "present_value_amount",
            "goal_value_pv",
            "target_pv",
            "amount_needed",
            "goal_value_fv",
            "target_fv",
        ),
        target_date=_goal_target_date(goal),
        invested_amount=invested_amount,
        current_value=current_value,
        monthly_contribution=_first_float(goal, "monthly_contribution"),
        suggested_contribution=None,
        priority=_enum_value(getattr(goal, "priority", None), "HIGH"),
        status=_enum_value(getattr(goal, "status", None), "ACTIVE"),
        goal_type=goal_type_str,
        inflation_rate=_first_float(goal, "inflation_rate", "inflation_annual"),
        notes=getattr(goal, "notes", None),
        time_to_goal_months=getattr(goal, "time_to_goal_months", None),
        amount_needed=_first_float(goal, "amount_needed"),
        goal_priority=getattr(goal, "goal_priority", None),
        investment_goal=getattr(goal, "investment_goal", None),
        created_at=goal.created_at,
        updated_at=goal.updated_at,
    )
