"""Live "thinking aloud" feed for a chat turn.

While ``ChatBrain.run_turn`` executes inside the send-message POST, the brain
and each flow publish short customer-facing lines describing what is actually
happening right now (reading context, classifying, which module is running).
The frontend polls ``GET /chat/sessions/{id}/thinking`` and renders the lines
as a live thinking bubble that disappears when the real reply arrives.

Thin wrapper over ``app.core.progress`` (same in-process store the invest
computes use) keyed per session so concurrent sessions never cross streams.
"""

from __future__ import annotations

import logging

from app.core.progress import ProgressView, clear_progress, get_progress, set_progress

logger = logging.getLogger(__name__)


def _task(session_id: object) -> str:
    return f"chat_turn:{session_id}"


def publish_thinking(
    user_id: object, session_id: object, pct: float, message: str
) -> None:
    """Best-effort: a thinking line must never break the turn."""
    if user_id is None or session_id is None:
        return
    try:
        set_progress(user_id, _task(session_id), pct, message)
    except Exception:  # pragma: no cover - store is in-memory, can't realistically fail
        logger.exception("publish_thinking failed (non-fatal)")


def publish_turn_thinking(turn, pct: float, message: str) -> None:
    """Publish using a ``ChatTurnInput``'s identity — the flows' entry point."""
    publish_thinking(
        getattr(turn, "effective_user_id", None),
        getattr(turn, "session_id", None),
        pct,
        message,
    )


def clear_thinking(user_id: object, session_id: object) -> None:
    if user_id is None or session_id is None:
        return
    clear_progress(user_id, _task(session_id))


def get_thinking(user_id: object, session_id: object) -> ProgressView:
    return get_progress(user_id, _task(session_id))
