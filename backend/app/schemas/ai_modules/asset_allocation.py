"""Pydantic schema — `asset_allocation.py`.

Request/response or DTO shapes for API validation and OpenAPI documentation. Kept separate from ORM models so API contracts can evolve independently of database columns.
"""


from __future__ import annotations

from pydantic import BaseModel, Field


class AssetAllocationRequest(BaseModel):
    question: str = Field(..., min_length=1)


class AssetAllocationResponse(BaseModel):
    answer_markdown: str
