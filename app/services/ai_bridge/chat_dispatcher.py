"""Per-intent chat handler registry + dispatcher.

Each chat-facing intent has exactly one handler module that registers itself
via @register(intent) at import time. The handler receives a TurnContext and
returns a ChatHandlerResult (text + optional snapshot/recommendation IDs).

This replaces ``followup_dispatcher.py`` — the new signature passes only
the TurnContext (handlers pull last_agent_runs from there themselves), so
first-turn handlers (no AgentRun yet) and follow-up handlers share one
entry point.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from app.services.chat_core.turn_context import TurnContext


@dataclass(frozen=True)
class ChatHandlerResult:
    """Return shape for every chat handler. Forwarded to ChatBrainResult."""
    text: str
    snapshot_id: uuid.UUID | None = None
    rebalancing_recommendation_id: uuid.UUID | None = None
    rebalancing_response: Any | None = None


Handler = Callable[["TurnContext"], Awaitable[ChatHandlerResult]]

_HANDLERS: dict[str, Handler] = {}


def register(intent: str) -> Callable[[Handler], Handler]:
    """Register a chat handler for the given intent. Stackable."""
    def decorator(fn: Handler) -> Handler:
        _HANDLERS[intent] = fn
        return fn
    return decorator


async def dispatch_chat(
    intent: str, turn_context: "TurnContext",
) -> ChatHandlerResult:
    """Look up the handler for ``intent`` and invoke it."""
    handler = _HANDLERS.get(intent)
    if handler is None:
        raise RuntimeError(
            f"No chat handler registered for intent={intent!r}"
        )
    return await handler(turn_context)
