from typing import Literal, Optional

from pydantic import BaseModel, Field


class ConversationTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class PortfolioQueryInput(BaseModel):
    question: str
    conversation_history: list[ConversationTurn] = Field(default_factory=list)


class PortfolioQueryResponse(BaseModel):
    answer: Optional[str] = None
    guardrail_triggered: bool
    redirect_message: Optional[str] = None
