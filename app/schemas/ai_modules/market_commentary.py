"""Pydantic schema — `market_commentary.py`.

Request/response or DTO shapes for API validation and OpenAPI documentation. Kept separate from ORM models so API contracts can evolve independently of database columns.
"""


from __future__ import annotations

from pydantic import BaseModel


class MarketCommentaryResponse(BaseModel):
    document_markdown: str
