"""Shared helpers for the ai_bridge layer (path setup, history formatting)."""

from __future__ import annotations

import sys
from pathlib import Path

# Resolved once; re-used by ensure_ai_agents_path().
_AI_AGENTS_SRC = str((Path(__file__).resolve().parents[3] / "AI_Agents" / "src"))


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
        label = "Customer" if msg["role"] == "user" else "Prozpr"
        lines.append(f"{label}: {msg['content']}")
    lines.append("---")
    return "\n".join(lines)
