"""Request/response payloads for the in-chat planning endpoints."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class SavedField(BaseModel):
    """One thing written on a turn, as the customer is shown it."""

    field_key: str
    label: str
    display_value: str
    # Present when the value was worked out from one we already held — "20%
    # increase on the ₹30,00,000 on file". Shown so they can catch a change
    # applied to the wrong starting figure.
    basis: Optional[str] = None


class UndoResponse(BaseModel):
    ok: bool = True
    field_key: Optional[str] = None
    # What was put back, in words — a field name, or the goal's own name.
    label: Optional[str] = None
    restored_to: Optional[Any] = None


class PlanningStateResponse(BaseModel):
    """Enough to re-render the conversation's plan state after a reload."""

    pending_question: Optional[str] = None
    pending_field_key: Optional[str] = None
    # The stage of a goal being built, when there is one: collecting /
    # confirming / follow_up.
    goal_in_progress: Optional[str] = None
    asks_used: int = 0
    asks_allowed: int = 0
    completeness: dict[str, Any] = Field(default_factory=dict)
