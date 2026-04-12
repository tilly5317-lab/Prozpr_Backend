"""Pydantic schema — `intent_classifier.py`.

Request/response or DTO shapes for API validation and OpenAPI documentation. Kept separate from ORM models so API contracts can evolve independently of database columns.
"""


from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from app.schemas.ai_modules.conversation import ConversationTurn


class IntentClassifyRequest(BaseModel):
    message: str = Field(..., min_length=1)
    conversation_history: list[ConversationTurn] = Field(default_factory=list)


class IntentClassifyResponse(BaseModel):
    intent: str
    confidence: float
    reasoning: str
    out_of_scope_message: Optional[str] = None
