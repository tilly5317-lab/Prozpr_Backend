"""Application service — `chat_service.py`.

Encapsulates business logic consumed by FastAPI routers. Uses database sessions, optional external APIs, and other services; should remain free of route-specific HTTP details (status codes live in routers).
"""


from __future__ import annotations

from app.services.ai_bridge import (
    classify_user_message,
    format_intent_response,
    generate_general_chat_response,
    generate_market_commentary,
    generate_portfolio_optimisation_response,
    generate_portfolio_query_response,
)

__all__ = [
    "classify_user_message",
    "format_intent_response",
    "generate_general_chat_response",
    "generate_market_commentary",
    "generate_portfolio_optimisation_response",
    "generate_portfolio_query_response",
]
