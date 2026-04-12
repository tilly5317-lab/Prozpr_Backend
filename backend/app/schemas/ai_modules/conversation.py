"""Pydantic schema — `conversation.py`.

Request/response or DTO shapes for API validation and OpenAPI documentation. Kept separate from ORM models so API contracts can evolve independently of database columns.
"""


from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class ConversationTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str
