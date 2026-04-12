"""AI bridge — `__init__.py`.

Sits between FastAPI services/routers and the ``AI_Agents/src`` packages (added to `sys.path` via ``ensure_ai_agents_path``). Handles env keys, async/thread boundaries, and user-context mapping. Ideal mutual fund allocation is invoked from here using ``Ideal_asset_allocation`` inside the app layer (e.g. ``ideal_allocation_runner``) so `AI_Agents` files stay untouched.
"""


from app.services.ai_bridge.asset_allocation_service import (
    generate_portfolio_optimisation_response,
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
    "generate_portfolio_optimisation_response",
    "generate_portfolio_query_response",
]
