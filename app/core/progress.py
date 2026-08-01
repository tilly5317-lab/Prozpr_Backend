"""In-process progress store for synchronous compute endpoints.

Some long computes (rebalancing plan, SIP fund split) run inside a single HTTP
request. While the POST is in flight the frontend polls a companion GET which
reads this store, so the user sees the pipeline's real stage + % instead of an
indeterminate spinner.

Single-instance only (same trade-off as ``mf_ingest_router``'s in-memory
cooldowns) — entries live in process memory. Every entry carries a timestamp
and expires after ``_TTL_S`` so a crashed compute can never present as stuck
"active"; ``clear_progress`` in the endpoint's ``finally`` is the normal exit.
"""

from __future__ import annotations

import time
from threading import Lock
from typing import Optional, TypedDict

_TTL_S = 300.0

_lock = Lock()
# (user, task) -> (ts, pct, [stage messages so far, oldest first])
_store: dict[tuple[str, str], tuple[float, float, list[str]]] = {}


class ProgressView(TypedDict):
    active: bool
    progress_pct: float
    message: Optional[str]
    messages: list[str]


def set_progress(user_id: object, task: str, pct: float, message: str) -> None:
    """Record a stage. The full message HISTORY is kept (not just the latest)
    so a poller can render every completed stage even when stages flip faster
    than its poll interval."""
    with _lock:
        key = (str(user_id), task)
        entry = _store.get(key)
        now = time.monotonic()
        messages: list[str] = (
            list(entry[2]) if entry is not None and (now - entry[0]) <= _TTL_S else []
        )
        if not messages or messages[-1] != message:
            messages.append(message)
        _store[key] = (now, float(pct), messages)


def clear_progress(user_id: object, task: str) -> None:
    with _lock:
        _store.pop((str(user_id), task), None)


def get_progress(user_id: object, task: str) -> ProgressView:
    with _lock:
        entry = _store.get((str(user_id), task))
        if entry is None or (time.monotonic() - entry[0]) > _TTL_S:
            if entry is not None:
                _store.pop((str(user_id), task), None)
            return ProgressView(
                active=False, progress_pct=0.0, message=None, messages=[]
            )
        return ProgressView(
            active=True,
            progress_pct=entry[1],
            message=entry[2][-1] if entry[2] else None,
            messages=list(entry[2]),
        )
