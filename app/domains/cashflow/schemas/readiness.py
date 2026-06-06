"""Pydantic schema — cashflow readiness.

Response shape for ``GET /cashflow/readiness`` — describes which user inputs the
projection engine needs and whether each is present, so the frontend can gate
the goal-planning page and render the unlock form.
"""

from __future__ import annotations

from typing import List, Optional, Union

from pydantic import BaseModel


class CashflowReadinessField(BaseModel):
    key: str
    label: str
    group: str
    kind: str  # "money" | "int" | "percent" | "date"
    unit: Optional[str] = None
    help: Optional[str] = None
    optional: bool = False
    present: bool
    value: Optional[Union[float, str]] = None


class CashflowReadinessResponse(BaseModel):
    ready: bool
    missing: List[str]
    fields: List[CashflowReadinessField]
