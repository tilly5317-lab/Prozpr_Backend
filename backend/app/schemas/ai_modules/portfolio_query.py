"""Pydantic schema — `portfolio_query.py`.

Request/response or DTO shapes for API validation and OpenAPI documentation. Kept separate from ORM models so API contracts can evolve independently of database columns.
"""


from __future__ import annotations

from pydantic import BaseModel, Field


class PortfolioQueryRequest(BaseModel):
    question: str = Field(..., min_length=1)


class PortfolioQueryResponse(BaseModel):
    answer_markdown: str
