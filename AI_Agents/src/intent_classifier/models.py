from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class Intent(str, Enum):
    ASSET_ALLOCATION = "asset_allocation"
    GOAL_PLANNING = "goal_planning"
    STOCK_ADVICE = "stock_advice"
    PORTFOLIO_QUERY = "portfolio_query"
    GENERAL_MARKET_QUERY = "general_market_query"
    REBALANCING = "rebalancing"
    ADDITIONAL_INVESTMENT = "additional_investment"
    MUTUAL_FUND_QUERY = "mutual_fund_query"
    OUT_OF_SCOPE = "out_of_scope"


class Tool(str, Enum):
    """Data the classifier declares and the caller fetches.

    New members also need ``_ToolLiteral`` in classifier.py — a drift test enforces it.
    """

    MARKET_COMMENTARY = "market_commentary"


class OutOfScopeSubreason(str, Enum):
    GIBBERISH = "gibberish"
    IDENTITY_OR_META = "identity_or_meta"
    SECURITY_OR_CREDENTIALS = "security_or_credentials"
    CHAT_SUMMARY = "chat_summary"
    OFF_TOPIC = "off_topic"
    OTHER = "other"


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
    out_of_scope_subreason: Optional[OutOfScopeSubreason] = None
    tools_needed: list[Tool] = Field(default_factory=list)  # nothing consumes this yet
