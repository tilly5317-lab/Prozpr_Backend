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
    "mutual_fund_query": ("Mutual_Fund_Query.md",),
}

# Per module: which of ITS action modes carry the methodology doc. The doc
# grounds "how/why does this work" answers, and each module's explain-the-why
# answer lives on a differently-named mode — so this is per module, not one
# global set. Modes NOT listed (compute, screen, consolidate, gather, …) stay
# lean: a freshly-computed plan or a ranked list doesn't carry the thesis.
_MODULE_DOC_MODES: dict[str, frozenset[str]] = {
    "rebalancing": frozenset({"narrate", "educate"}),
    "asset_allocation": frozenset({"narrate", "educate"}),
    "goal_planning": frozenset({"narrate"}),  # no educate mode
    "mutual_fund_query": frozenset({"fund_detail"}),  # "why do we recommend this fund"
    "additional_investment": frozenset({"category_probe"}),  # "should I add <category>?"
}


@lru_cache(maxsize=None)
def _read_doc(filename: str) -> str | None:
    try:
        text = (_DOCS_DIR / filename).read_text(encoding="utf-8").strip()
    except OSError:
        logger.warning("logic_doc_unreadable file=%s", _DOCS_DIR / filename)
        return None
    return text or None


def get_logic_reference(module_name: str) -> str | None:
    """Concatenated thesis doc(s) for ``module_name``, or ``None``. Mode-agnostic
    loader — callers that gate by mode should use ``logic_reference_for``."""
    filenames = _MODULE_DOCS.get(module_name)
    if not filenames:
        return None
    parts = [text for text in (_read_doc(f) for f in filenames) if text]
    return "\n\n---\n\n".join(parts) or None


def logic_reference_for(module_name: str, action_mode: str) -> str | None:
    """The module's methodology doc, but only for the mode(s) that carry it.

    ``None`` when the module has no doc, or ``action_mode`` isn't one of the
    module's doc-carrying modes (keeps computed plans / ranked lists lean).
    """
    if action_mode not in _MODULE_DOC_MODES.get(module_name, frozenset()):
        return None
    return get_logic_reference(module_name)
