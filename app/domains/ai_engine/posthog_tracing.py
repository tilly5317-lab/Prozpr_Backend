"""Per-turn PostHog LLM observability (traces, generations, cost, tokens).

Attaches PostHog's LangChain ``CallbackHandler`` to every LangChain/LangGraph
call made while a turn is active, using the same ``register_configure_hook`` +
ContextVar mechanism as ``usage_tracking`` next door. That is what makes this
zero-touch: no call site passes ``config={"callbacks": [...]}``, so all ~20
``ChatAnthropic`` sites and every LangGraph node are covered without edits, and
new call sites are covered automatically the day they are written.

Why a fresh handler per turn rather than one shared instance: the handler holds
``distinct_id`` / ``trace_id`` as instance state, so a shared one would attribute
every user's generations to whoever constructed it. ContextVars are task-local,
so concurrent turns in different asyncio tasks each see only their own handler.
(PostHog does not document handler thread-safety either way; per-turn
construction sidesteps the question. It is four field assignments — cheap.)

What lands in PostHog per turn: one ``$ai_trace`` root, an ``$ai_span`` per
LangGraph node / chain step, and an ``$ai_generation`` per LLM call carrying
model, token counts, latency, tool definitions, and stop reason. Cost is derived
server-side by PostHog from model + tokens; the SDK does not send it.

Prompts, completions, and LangGraph state are redacted unless
``POSTHOG_LLM_CAPTURE_CONTENT=true`` — see ``Settings.posthog_llm_capture_content``.
Everything here is best-effort: observability must never break a chat turn.
"""

from __future__ import annotations

import logging
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Iterator

from langchain_core.callbacks.base import BaseCallbackHandler
from langchain_core.tracers.context import register_configure_hook

logger = logging.getLogger(__name__)

_posthog_cb_var: ContextVar[BaseCallbackHandler | None] = ContextVar(
    "ailax_posthog_llm_tracing", default=None
)
# Registered exactly once at import — see usage_tracking's note on why the
# stock get_usage_metadata_callback helper leaks a hook per use.
register_configure_hook(_posthog_cb_var, inheritable=True)


def _build_handler(
    *,
    distinct_id: str | None,
    trace_id: str | None,
    properties: dict[str, Any] | None,
) -> BaseCallbackHandler | None:
    """Construct a PostHog CallbackHandler, or None when capture is unavailable."""
    from app.core.observability import get_posthog_client

    client = get_posthog_client()
    if client is None:
        return None

    try:
        from posthog.ai.langchain import CallbackHandler
    except ImportError:
        # posthog installed without langchain-core visible, or too old a posthog
        # (the langchain module needs >= 6.7.13 for LangChain 1.x imports).
        logger.warning(
            "posthog.ai.langchain unavailable; LLM traces will not be captured."
        )
        return None

    return CallbackHandler(
        client=client,
        distinct_id=distinct_id,
        trace_id=trace_id or str(uuid.uuid4()),
        properties=properties or {},
    )


@contextmanager
def track_turn_posthog(
    *,
    distinct_id: str | None = None,
    trace_id: str | None = None,
    properties: dict[str, Any] | None = None,
) -> Iterator[None]:
    """Capture every LLM call in this turn as a PostHog trace.

    ``distinct_id`` ties generations to a user; omitting it makes the events
    anonymous (PostHog sets ``$process_person_profile: False`` and falls back to
    the run UUID) rather than misattributing them. ``trace_id`` groups a turn's
    generations — pass the chat session/turn id to make a turn navigable as one
    trace; a UUID is generated when omitted.

    A no-op when PostHog is not configured, and never raises: a failure to build
    the handler must not take down the chat turn.
    """
    handler: BaseCallbackHandler | None = None
    try:
        handler = _build_handler(
            distinct_id=distinct_id, trace_id=trace_id, properties=properties
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("PostHog LLM handler init failed (continuing): %s", exc)

    if handler is None:
        yield
        return

    token = _posthog_cb_var.set(handler)
    try:
        yield
    finally:
        _posthog_cb_var.reset(token)
