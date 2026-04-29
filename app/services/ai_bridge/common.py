"""Shared helpers for the ai_bridge layer (path setup, history, tracing)."""

from __future__ import annotations

import sys
from pathlib import Path

_AI_AGENTS_SRC = str((Path(__file__).resolve().parents[3] / "AI_Agents" / "src"))
_TRACE_PREFIX = "[AILAX_TRACE]"


def ensure_ai_agents_path() -> None:
    """Add ``AI_Agents/src`` to sys.path so we can import agent packages."""
    if _AI_AGENTS_SRC not in sys.path:
        sys.path.insert(0, _AI_AGENTS_SRC)


def build_history_block(history: list[dict[str, str]] | None) -> str:
    """Format the last 6 conversation turns into a text block for LLM prompts."""
    if not history:
        return ""
    lines = ["--- Recent Conversation History ---"]
    for msg in history[-6:]:
        label = "Customer" if msg["role"] == "user" else "AILAX"
        lines.append(f"{label}: {msg['content']}")
    lines.append("---")
    return "\n".join(lines)


def trace_line(message: str) -> None:
    """Print ``message`` prefixed with ``[AILAX_TRACE]`` for server-side debugging."""
    print(f"{_TRACE_PREFIX} {message}", flush=True)


def trace_response_preview(label: str, text: str, max_chars: int = 600) -> None:
    """Trace a single-line preview of ``text``, truncated to ``max_chars``."""
    t = (text or "").strip().replace("\n", " ")
    if len(t) > max_chars:
        t = t[:max_chars] + "…"
    trace_line(f"{label} (preview): {t}")
