"""Shared helpers for the ai_bridge layer (path setup, history, tracing, money formatting).

``format_inr_indian`` lives in ``AI_Agents/src/common.py`` and is re-exported
here so that ai_bridge consumers (facts-pack builders, chat formatters) can
keep importing it from this module unchanged.
"""

from __future__ import annotations

import sys
from datetime import timedelta
from pathlib import Path
from typing import Any

# project_root/app/domains/ai_engine/common.py → parents[3] is project root.
_AI_AGENTS_SRC = str((Path(__file__).resolve().parents[3] / "AI_Agents" / "src"))
_TRACE_PREFIX = "[AILAX_TRACE]"

# Inject AI_Agents/src into sys.path at module-import time so the re-export
# below resolves. Callers can still call ``ensure_ai_agents_path()`` later as
# a no-op for backward compatibility.
if _AI_AGENTS_SRC not in sys.path:
    sys.path.insert(0, _AI_AGENTS_SRC)

from common import (  # noqa: E402  re-exports
    RISK_CATEGORIES as RISK_CATEGORIES,
    category_for_effective_risk_score as category_for_effective_risk_score,
    format_inr_indian as format_inr_indian,
)


def asset_class_for_subgroup(subgroup: str | None) -> str:
    """Map engine asset_subgroup → high-level asset class (equity / debt / others).

    Delegates to the canonical subgroup→class map in
    ``mutual_funds/services/scheme_classification.py`` (the single source) and
    lowercases the result to preserve this function's long-standing lowercase
    contract for chat facts-pack builders and the rebalancing chat summary.
    Previously this kept its own narrow copy that mislabeled debt-duration
    subgroups (near_debt, medium_debt, …) and china_equities as "others".
    """
    from app.domains.mutual_funds.services.scheme_classification import (
        asset_class_for_subgroup as _canonical,
    )

    return _canonical(subgroup).lower()


def ensure_ai_agents_path() -> None:
    """Add ``AI_Agents/src`` to sys.path so we can import agent packages."""
    if _AI_AGENTS_SRC not in sys.path:
        sys.path.insert(0, _AI_AGENTS_SRC)


# A same-session gap this long means the customer left and came back. Sessions
# here are long-lived threads, not single sittings, so without a time signal a
# fortnight-old question reads as live context — which is how the 2026-07-25
# turn inherited a 13-day-old goal question.
_SESSION_GAP = timedelta(hours=24)


def gap_note(previous_at, asked_at) -> str | None:
    """``"13 days later"`` when two turns straddle a break, else ``None``."""
    if previous_at is None or asked_at is None:
        return None
    gap = asked_at - previous_at
    if gap < _SESSION_GAP:
        return None
    # Rounded, not floored: timedelta.days truncates, so a 47-hour break would
    # read as "1 day later" and a 12d23h one as "12 days later".
    days = round(gap / timedelta(days=1))
    return f"{days} days later" if days > 1 else "1 day later"


def with_gap_notes(history: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Fold each gap note into the content of the turn that resumed the thread.

    For consumers that rebuild history into their own ``{role, content}`` message
    models — the intent classifier's ``ConversationMessage`` and portfolio_query's
    ``ConversationTurn`` — where a standalone marker line has nowhere to live.
    Entries are copied, never mutated. History without ``asked_at`` passes
    through untouched.
    """
    if not history:
        return []
    annotated: list[dict[str, Any]] = []
    previous_at = None
    for msg in history:
        asked_at = msg.get("asked_at")
        note = gap_note(previous_at, asked_at)
        if asked_at is not None:
            previous_at = asked_at
        if note:
            msg = {**msg, "content": f"[{note}] {msg.get('content', '')}"}
        annotated.append(msg)
    return annotated


def build_history_block(history: list[dict[str, Any]] | None) -> str:
    """Format the last 6 conversation turns into a text block for LLM prompts.

    Entries carrying ``asked_at`` (everything from ``load_conversation_history``)
    get a marker inserted wherever consecutive turns are more than
    ``_SESSION_GAP`` apart. Hand-built history without it renders unchanged.
    """
    if not history:
        return ""
    lines = ["--- Recent Conversation History ---"]
    previous_at = None
    for msg in history[-6:]:
        asked_at = msg.get("asked_at")
        note = gap_note(previous_at, asked_at)
        if asked_at is not None:
            previous_at = asked_at
        if note:
            lines.append(f"--- {note} ---")
        label = "Customer" if msg["role"] == "user" else "PI"
        lines.append(f"{label}: {msg['content']}")
    lines.append("---")
    return "\n".join(lines)


# Number of recent turns surfaced to per-module follow-up classifiers
# (asset_allocation/chat.py and rebalancing/chat.py). Distinct from the
# formatter-side history block, which is consumed inside the answer prompt.
DETECT_HISTORY_TURNS = 6


def build_detect_history_block(history: list[dict[str, str]] | None) -> str:
    """Format history for follow-up classifier prompts.

    Raw role labels (no "Customer"/"Prozpr" relabeling), no frame markers, last
    ``DETECT_HISTORY_TURNS`` turns, empty-content turns filtered. Used by the
    per-module follow-up classifiers; distinct from ``build_history_block``
    which targets the formatter prompt.
    """
    if not history:
        return ""
    recent = history[-DETECT_HISTORY_TURNS:]
    lines = [
        f"{m.get('role', 'user')}: {m.get('content', '')}".strip()
        for m in recent
        if m.get("content")
    ]
    return "\n".join(lines)


def trace_line(message: str) -> None:
    """Print ``message`` prefixed with ``[AILAX_TRACE]`` for server-side debugging.

    Best-effort only: a debug trace must NEVER break the request it is tracing.
    On a console whose encoding can't represent the text — e.g. Windows cp1252
    stdout vs the ``₹`` sign in money traces — a plain ``print`` raises
    ``UnicodeEncodeError`` and would bubble up to a 500. Degrade gracefully
    instead: write UTF-8 bytes straight to the buffer (preserving ``₹``), and if
    even that fails, swallow it rather than fail the request.
    """
    line = f"{_TRACE_PREFIX} {message}"
    try:
        print(line, flush=True)
    except UnicodeEncodeError:
        try:
            buffer = getattr(sys.stdout, "buffer", None)
            if buffer is not None:
                buffer.write((line + "\n").encode("utf-8", errors="replace"))
                buffer.flush()
            else:
                enc = getattr(sys.stdout, "encoding", None) or "ascii"
                print(line.encode(enc, "replace").decode(enc, "replace"), flush=True)
        except Exception:
            pass
    except Exception:
        # Never let server-side tracing break a request.
        pass


def trace_response_preview(label: str, text: str, max_chars: int = 600) -> None:
    """Trace a single-line preview of ``text``, truncated to ``max_chars``."""
    t = (text or "").strip().replace("\n", " ")
    if len(t) > max_chars:
        t = t[:max_chars] + "…"
    trace_line(f"{label} (preview): {t}")
