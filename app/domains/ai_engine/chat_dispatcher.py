"""Per-intent chat handler registry + dispatcher.

Each chat-facing intent has exactly one handler module that registers itself
via @register(intent) at import time. The handler receives a TurnContext and
returns a ChatHandlerResult (text + optional snapshot/recommendation IDs).

This replaces ``followup_dispatcher.py`` — the new signature passes only
the TurnContext (handlers pull last_agent_runs from there themselves), so
first-turn handlers (no AgentRun yet) and follow-up handlers share one
entry point.

Speculative action-detection (audit F4): a module may also register its
follow-up action detector via @register_speculative_detector(intent). The
brain starts it CONCURRENTLY with the intent classifier when the session's
active intent matches, and attaches the running task to
``TurnContext.speculative_detect`` only when the classifier confirms the same
intent — so the handler saves a full serial LLM round trip on follow-ups.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from app.domains.ai_engine.turn_context import TurnContext

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ChatHandlerResult:
    """Return shape for every chat handler. Forwarded to ChatBrainResult."""

    text: str
    snapshot_id: uuid.UUID | None = None
    asset_allocation_run_id: uuid.UUID | None = None
    rebalancing_recommendation_id: uuid.UUID | None = None
    rebalancing_run_id: uuid.UUID | None = None
    rebalancing_response: Any | None = None
    additional_investment_run_id: uuid.UUID | None = None
    chart_payloads: list[dict[str, Any]] | None = None


Handler = Callable[["TurnContext"], Awaitable[ChatHandlerResult]]

_HANDLERS: dict[str, Handler] = {}


def register(intent: str) -> Callable[[Handler], Handler]:
    """Register a chat handler for the given intent. Stackable."""

    def decorator(fn: Handler) -> Handler:
        _HANDLERS[intent] = fn
        return fn

    return decorator


async def dispatch_chat(
    intent: str,
    turn_context: "TurnContext",
) -> ChatHandlerResult:
    """Look up the handler for ``intent`` and invoke it."""
    handler = _HANDLERS.get(intent)
    if handler is None:
        raise RuntimeError(f"No chat handler registered for intent={intent!r}")
    return await handler(turn_context)


# ---------------------------------------------------------------------------
# Speculative action-detection registry
# ---------------------------------------------------------------------------

# A detector takes the TurnContext and returns the module's own action model
# (e.g. ChatAction / RebalanceAction), or None when not applicable (no prior
# run in the session). It must not have side effects — the brain may discard
# the result when the classifier picks a different intent.
SpeculativeDetector = Callable[["TurnContext"], Awaitable[Any]]

_SPECULATIVE_DETECTORS: dict[str, SpeculativeDetector] = {}


def register_speculative_detector(
    intent: str,
) -> Callable[[SpeculativeDetector], SpeculativeDetector]:
    """Register a module's follow-up action detector for speculative runs."""

    def decorator(fn: SpeculativeDetector) -> SpeculativeDetector:
        _SPECULATIVE_DETECTORS[intent] = fn
        return fn

    return decorator


def speculative_detector_for(intent: str) -> SpeculativeDetector | None:
    return _SPECULATIVE_DETECTORS.get(intent)


async def consume_speculative_detect(ctx: "TurnContext") -> Any | None:
    """Await the brain-attached speculative detect result, or None to go serial.

    Any failure (task errored, cancelled, returned None) degrades to None so
    the caller falls back to its own serial detect call — behaviour is then
    identical to speculation never having run. Deliberately does NOT catch
    CancelledError: the task is checked for cancellation before awaiting, so a
    CancelledError raised mid-await belongs to the CALLER's own cancellation
    (e.g. the brain's flow timeout) and must propagate.
    """
    task: asyncio.Task | None = getattr(ctx, "speculative_detect", None)
    if task is None or task.cancelled():
        return None
    try:
        return await task
    except Exception:
        logger.warning(
            "speculative detect failed; falling back to serial detect",
            exc_info=True,
        )
        return None
