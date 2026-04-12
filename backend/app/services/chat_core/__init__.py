"""Chat core — `__init__.py`.

Orchestrates a single user turn: intent classification, branch routing (market, portfolio query, portfolio-style spine with liquidity gate and allocation), optional telemetry, and assistant text. Depends on ``services.ai_bridge`` and preloaded ORM user context from ``get_ai_user_context``.
"""


from app.services.chat_core.brain import ChatBrain
from app.services.chat_core.types import ChatBrainResult, ChatTurnInput

__all__ = [
    "ChatBrain",
    "ChatBrainResult",
    "ChatTurnInput",
]
