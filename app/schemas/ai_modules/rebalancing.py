"""Pydantic schema — `rebalancing.py`.

Request/response shapes for ``POST /api/v1/ai-modules/rebalancing/compute``.
Lets the frontend (or curl) trigger a rebalancing run without going through
the chat surface; the underlying service is the same one used by chat.
"""

from __future__ import annotations

from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field


class RebalancingComputeApiRequest(BaseModel):
    question: str = Field(default="rebalance my portfolio", min_length=1)


class RebalancingComputeApiResponse(BaseModel):
    answer_markdown: str
    recommendation_id: Optional[UUID] = None
    allocation_snapshot_id: Optional[UUID] = None
    used_cached_allocation: bool
    blocking_message: Optional[str] = None
