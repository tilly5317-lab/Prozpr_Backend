"""Public surface of the ai_bridge package — re-exports the five main service functions."""


from app.services.ai_bridge.asset_allocation import (
    generate_asset_allocation_response,
)
from app.services.ai_bridge.general_chat_service import generate_general_chat_response
from app.services.ai_bridge.intent_classifier_service import (
    classify_user_message,
    format_intent_response,
)
from app.services.ai_bridge.market_commentary_service import generate_market_commentary
from app.services.ai_bridge.portfolio_query_service import generate_portfolio_query_response

__all__ = [
    "classify_user_message",
    "format_intent_response",
    "generate_general_chat_response",
    "generate_market_commentary",
    "generate_asset_allocation_response",
    "generate_portfolio_query_response",
]
