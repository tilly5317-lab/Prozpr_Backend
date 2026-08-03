"""Token stream for a chat turn's final answer.

Every customer-facing answer surface produces its text through a forced tool
call, so the reply arrives as partial JSON inside a tool argument rather than as
plain text. This module owns the machinery to turn that into incremental deltas
and hand them to whoever is streaming the turn.

Lives here, not under ``app/``, because one of those surfaces is an agent in
``AI_Agents/src`` (``portfolio_query/llm_client.py``) and agents must not import
the app layer. Same arrangement as ``persona.py`` / ``reasoned_reply.py``:
canonical definition here, re-exported by ``app/domains/ai_engine/streaming.py``.
Imports no peer agent, and langchain only lazily.

Identity is carried in a ContextVar rather than threaded through arguments —
the answer-producing call sites sit five-plus frames below the request handler
and neither of them takes a ``TurnContext``. ``asyncio.create_task`` copies the
context, so a turn spawned inside ``open_token_stream`` inherits the sink.

NOTHING STREAMS UNLESS A STREAM IS OPEN. With no open stream the call sites keep
using ``ainvoke`` on exactly the path they use today, so the existing
non-streaming endpoint is byte-for-byte unaffected.

Single-instance only, same trade-off as ``app.core.progress``: the sink lives in
process memory, so the streaming request and the turn must share a worker. The
Dockerfile runs one uvicorn worker with no ``--workers``.

Intended use from a future SSE endpoint::

    async with open_token_stream() as stream:
        turn = asyncio.create_task(ChatBrain().run_turn(...))
        turn.add_done_callback(lambda _: stream.close())
        async for delta in stream:
            yield sse_event("delta", delta)
        result = await turn   # authoritative: may differ from the deltas

Deltas are PROVISIONAL. The formatter discards its whole response and returns a
deterministic fallback when the model truncates at max_tokens
(``answer_formatter.formatter``), and general_chat renders the answer field into
a final shape before returning it. Whatever ``run_turn`` returns wins; a client
that has rendered deltas must be prepared to replace them.
"""

from __future__ import annotations

import asyncio
import contextvars
import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

logger = logging.getLogger(__name__)

_CLOSED = object()

# Load-bearing for streaming a FORCED TOOL CALL. By default the API buffers a
# tool call's input JSON server-side and flushes it in one burst near the end of
# the response, which makes token streaming nearly pointless: measured on a
# ~4,500-char answer, the first 10% of text arrived 11.55s into a 15.86s call
# (27% of the call left to read into). With this beta the same answer delivered
# 10% by 2.71s of 11.94s — 77%, matching plain-text streaming.
#
# The trade-off the beta buys: input JSON is no longer validated before it is
# sent, so a malformed tool call is possible. Both call sites already degrade
# safely — the final parse fails, and the module falls back to its deterministic
# brief — so the cost of that is a lost formatter run, not a broken reply.
FINE_GRAINED_TOOL_STREAMING = "fine-grained-tool-streaming-2025-05-14"


class TokenStream:
    """Async-iterable sink of answer-text deltas for one turn."""

    def __init__(self) -> None:
        # Unbounded deliberately. Bounding it would mean dropping deltas from
        # the middle of an answer, which silently corrupts the rendered text;
        # the producer is capped by the call site's max_tokens anyway.
        self._queue: asyncio.Queue[Any] = asyncio.Queue()
        self._closed = False

    def publish(self, delta: str) -> None:
        """Best-effort: a delta must never break the turn."""
        if self._closed or not delta:
            return
        try:
            self._queue.put_nowait(delta)
        except Exception:  # pragma: no cover - unbounded queue
            logger.exception("token delta publish failed (non-fatal)")

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._queue.put_nowait(_CLOSED)
        except Exception:  # pragma: no cover
            logger.exception("token stream close failed (non-fatal)")

    async def __aiter__(self) -> AsyncIterator[str]:
        while True:
            item = await self._queue.get()
            if item is _CLOSED:
                return
            yield item


_current: contextvars.ContextVar[TokenStream | None] = contextvars.ContextVar(
    "ai_engine_token_stream", default=None
)


def current_token_stream() -> TokenStream | None:
    return _current.get()


@asynccontextmanager
async def open_token_stream() -> AsyncIterator[TokenStream]:
    """Open a sink for the current context. Always closed on the way out so a
    consumer can never hang waiting on a turn that already died."""
    stream = TokenStream()
    token = _current.set(stream)
    try:
        yield stream
    finally:
        _current.reset(token)
        stream.close()


async def astream_tool_answer(llm: Any, messages: list, *, answer_field: str = "answer"):
    """Run ``llm.astream`` over a forced-tool call, publishing incremental text
    from ``answer_field`` to the open stream.

    Returns the accumulated message, which duck-types what ``ainvoke`` returns:
    callers keep reading ``.tool_calls`` and ``.response_metadata`` as before.
    """
    from langchain_core.utils.json import parse_partial_json

    stream = current_token_stream()
    final = None
    published = 0
    async for chunk in llm.astream(messages):
        final = chunk if final is None else final + chunk
        if stream is None:
            continue
        text = _partial_answer(final, answer_field, parse_partial_json)
        if text is not None and len(text) > published:
            stream.publish(text[published:])
            published = len(text)
    return final


def _partial_answer(message: Any, answer_field: str, parse_partial_json) -> str | None:
    """Pull the answer field out of a half-built tool call, or None if it has
    not started arriving yet. Malformed partial JSON is normal mid-stream."""
    for chunk in getattr(message, "tool_call_chunks", None) or []:
        raw = chunk.get("args") if isinstance(chunk, dict) else None
        if not raw:
            continue
        try:
            args = parse_partial_json(raw)
        except Exception:
            continue
        if isinstance(args, dict):
            value = args.get(answer_field)
            if isinstance(value, str):
                return value
    return None
