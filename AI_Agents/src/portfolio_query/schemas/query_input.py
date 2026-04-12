from typing import Literal
from pydantic import BaseModel, Field


class ConversationTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class PortfolioQueryInput(BaseModel):
    question: str
    conversation_history: list[ConversationTurn] = Field(default_factory=list)
