"""Pydantic schemas for the rebalancing readiness gate.

Mirrors ``app.domains.cashflow.schemas.readiness`` so the frontend gate can
render the unlock form straight from the ``GET /rebalancing/readiness`` payload.
"""

from __future__ import annotations

from typing import List, Optional, Union

from pydantic import BaseModel


class RebalancingReadinessField(BaseModel):
    key: str
    label: str
    group: str
    kind: str  # "date" | "money" | "int" | "percent"
    unit: Optional[str] = None
    help: Optional[str] = None
    optional: bool = False
    present: bool
    value: Optional[Union[float, str]] = None


class RebalancingReadinessResponse(BaseModel):
    ready: bool
    missing: List[str]
    fields: List[RebalancingReadinessField]
    # Whether the user has at least one MF holding. When false the UI shows a
    # "Connect your portfolio" action instead of a form field.
    has_holdings: bool
