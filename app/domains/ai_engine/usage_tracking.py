"""Per-turn LLM token-usage tracking (audit F9).

Aggregates ``AIMessage.usage_metadata`` across every LangChain chat-model call
made while a tracker is active — including sync invokes offloaded via
``asyncio.to_thread`` and calls buried inside ``with_structured_output`` (the
callback layer fires regardless of how the response object is consumed).

Why not ``langchain_core.callbacks.get_usage_metadata_callback``: that helper
creates a NEW ContextVar and permanently appends it to LangChain's global
``_configure_hooks`` list on EVERY use — one dead hook per chat turn, scanned
on every subsequent LLM call. Fine for a one-off script, a leak on a server.
Here the two ContextVars below are registered exactly once at import; per-turn
cost is a contextvar set/reset.

Two independent trackers exist so they can nest: the TURN tracker (wrapped
around the whole ``ChatBrain`` turn) still sees calls made inside the FORMATTER
tracker — each handler aggregates independently, so the turn row carries the
turn total and the formatter row its own share.

Concurrency: ContextVars are task-local, so concurrent turns in different
asyncio tasks each see only their own handler.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Iterator

from langchain_core.callbacks import UsageMetadataCallbackHandler
from langchain_core.tracers.context import register_configure_hook

_turn_usage_var: ContextVar[UsageMetadataCallbackHandler | None] = ContextVar(
    "ailax_turn_llm_usage", default=None
)
_formatter_usage_var: ContextVar[UsageMetadataCallbackHandler | None] = ContextVar(
    "ailax_formatter_llm_usage", default=None
)
register_configure_hook(_turn_usage_var, inheritable=True)
register_configure_hook(_formatter_usage_var, inheritable=True)


@contextmanager
def _track(
    var: ContextVar[UsageMetadataCallbackHandler | None],
) -> Iterator[UsageMetadataCallbackHandler]:
    cb = UsageMetadataCallbackHandler()
    token = var.set(cb)
    try:
        yield cb
    finally:
        var.reset(token)


@contextmanager
def track_turn_llm_usage() -> Iterator[UsageMetadataCallbackHandler]:
    """Aggregate usage for every LLM call made during one chat turn."""
    with _track(_turn_usage_var) as cb:
        yield cb


@contextmanager
def track_formatter_llm_usage() -> Iterator[UsageMetadataCallbackHandler]:
    """Aggregate usage for the answer-formatter call (nests inside the turn tracker)."""
    with _track(_formatter_usage_var) as cb:
        yield cb


def jsonable_llm_usage(usage_metadata: dict[str, Any] | None) -> dict[str, Any] | None:
    """Normalize the handler's per-model usage dict to plain JSON, or None if empty."""
    if not usage_metadata:
        return None
    try:
        return json.loads(json.dumps(usage_metadata, default=str))
    except (TypeError, ValueError):
        return None


def usage_totals(usage_metadata: dict[str, Any] | None) -> tuple[int, int]:
    """(input_tokens, output_tokens) summed across models, for compact log lines."""
    if not usage_metadata:
        return 0, 0
    tokens_in = sum(int(m.get("input_tokens") or 0) for m in usage_metadata.values())
    tokens_out = sum(int(m.get("output_tokens") or 0) for m in usage_metadata.values())
    return tokens_in, tokens_out
