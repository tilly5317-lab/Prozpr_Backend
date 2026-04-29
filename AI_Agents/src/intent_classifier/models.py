from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class Intent(str, Enum):
    PORTFOLIO_OPTIMISATION = "portfolio_optimisation"
    GOAL_PLANNING          = "goal_planning"
    STOCK_ADVICE           = "stock_advice"
    PORTFOLIO_QUERY        = "portfolio_query"
    GENERAL_MARKET_QUERY   = "general_market_query"
    REBALANCING            = "rebalancing"
    OUT_OF_SCOPE           = "out_of_scope"


class ConversationMessage(BaseModel):
    role: str  # "user" or "assistant"
    content: str


class ClassificationInput(BaseModel):
    customer_question: str
    conversation_history: list[ConversationMessage] = Field(default_factory=list)
    active_intent: Optional[Intent] = None


class ClassificationResult(BaseModel):
    intent: Intent
    confidence: float = Field(ge=0.0, le=1.0)
    is_follow_up: bool = False
    reasoning: str
    out_of_scope_message: Optional[str] = None
