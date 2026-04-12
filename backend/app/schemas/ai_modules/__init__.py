"""Pydantic schema — `__init__.py`.

Request/response or DTO shapes for API validation and OpenAPI documentation. Kept separate from ORM models so API contracts can evolve independently of database columns.
"""


from app.schemas.ai_modules.asset_allocation import AssetAllocationRequest, AssetAllocationResponse
from app.schemas.ai_modules.conversation import ConversationTurn
from app.schemas.ai_modules.intent_classifier import IntentClassifyRequest, IntentClassifyResponse
from app.schemas.ai_modules.market_commentary import MarketCommentaryResponse
from app.schemas.ai_modules.portfolio_query import PortfolioQueryRequest, PortfolioQueryResponse
from app.schemas.ai_modules.status import AIModuleStatusResponse

__all__ = [
    "AIModuleStatusResponse",
    "AssetAllocationRequest",
    "AssetAllocationResponse",
    "ConversationTurn",
    "IntentClassifyRequest",
    "IntentClassifyResponse",
    "MarketCommentaryResponse",
    "PortfolioQueryRequest",
    "PortfolioQueryResponse",
]
