"""Loader for the client-safe Logics reference docs.

Maps a chat module to its thesis doc(s) under
``AI_Agents/Reference_docs/Logics_reference_docs/`` so the shared answer
formatter can ground methodology questions ("how/why does the approach
work") in Prozpr's published philosophy instead of the LLM's general
knowledge.

The docs are deliberately client-safe — philosophy only, no proprietary
thresholds, caps or weights — so their text can sit in an LLM prompt
verbatim. Loaded once per process (they change only on deploy); a missing
or unreadable file degrades to ``None`` so a lost doc can never break a
reply.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

_DOCS_DIR = (
    Path(__file__).resolve().parents[3]
    / "AI_Agents"
    / "Reference_docs"
    / "Logics_reference_docs"
)

# module_name (as passed to format_with_telemetry) -> thesis docs, primary first.
_MODULE_DOCS: dict[str, tuple[str, ...]] = {
    "rebalancing": ("Rebalancing.md", "Practical_Asset_Allocation.md"),
    "asset_allocation": ("Asset_Allocation.md", "Risk_Profiling.md"),
    "goal_planning": ("Cashflow_Statement.md",),
    "additional_investment": ("Additional_Investment.md",),
}

# Action modes whose questions are about mechanism/meaning — the only modes
# that carry the docs. Compute/recompute replies stay lean and facts-driven.
LOGIC_DOC_MODES = frozenset({"educate", "narrate"})


@lru_cache(maxsize=None)
def _read_doc(filename: str) -> str | None:
    try:
        text = (_DOCS_DIR / filename).read_text(encoding="utf-8").strip()
    except OSError:
        logger.warning("logic_doc_unreadable file=%s", _DOCS_DIR / filename)
        return None
    return text or None


def get_logic_reference(module_name: str) -> str | None:
    """Concatenated thesis doc(s) for ``module_name``, or ``None``."""
    filenames = _MODULE_DOCS.get(module_name)
    if not filenames:
        return None
    parts = [text for text in (_read_doc(f) for f in filenames) if text]
    return "\n\n---\n\n".join(parts) or None
