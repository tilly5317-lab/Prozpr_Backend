"""Shared helpers for the ai_bridge layer (path setup, history, tracing, money formatting).

``format_inr_indian`` lives in ``AI_Agents/src/common.py`` and is re-exported
here so that ai_bridge consumers (facts-pack builders, chat formatters) can
keep importing it from this module unchanged.
"""

from __future__ import annotations

import sys
from pathlib import Path

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


def build_history_block(history: list[dict[str, str]] | None) -> str:
    """Format the last 6 conversation turns into a text block for LLM prompts."""
    if not history:
        return ""
    lines = ["--- Recent Conversation History ---"]
    for msg in history[-6:]:
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
    """Print ``message`` prefixed with ``[AILAX_TRACE]`` for server-side debugging."""
    print(f"{_TRACE_PREFIX} {message}", flush=True)


def trace_response_preview(label: str, text: str, max_chars: int = 600) -> None:
    """Trace a single-line preview of ``text``, truncated to ``max_chars``."""
    t = (text or "").strip().replace("\n", " ")
    if len(t) > max_chars:
        t = t[:max_chars] + "…"
    trace_line(f"{label} (preview): {t}")
