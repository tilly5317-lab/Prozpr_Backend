"""Pydantic schema — `__init__.py`.

Request/response or DTO shapes for API validation and OpenAPI documentation. Kept separate from ORM models so API contracts can evolve independently of database columns.
"""


from app.domains.ai_engine.schemas.conversation import ConversationTurn
from app.domains.ai_engine.schemas.intent_classifier import IntentClassifyRequest, IntentClassifyResponse
from app.domains.ai_engine.schemas.market_commentary import MarketCommentaryResponse
from app.domains.ai_engine.schemas.portfolio_query import PortfolioQueryRequest, PortfolioQueryResponse
from app.domains.ai_engine.schemas.rebalancing import (
    RebalancingComputeApiRequest,
    RebalancingComputeApiResponse,
)
from app.domains.ai_engine.schemas.status import AIModuleStatusResponse

__all__ = [
    "AIModuleStatusResponse",
    "ConversationTurn",
    "IntentClassifyRequest",
    "IntentClassifyResponse",
    "MarketCommentaryResponse",
    "PortfolioQueryRequest",
    "PortfolioQueryResponse",
    "RebalancingComputeApiRequest",
    "RebalancingComputeApiResponse",
]
