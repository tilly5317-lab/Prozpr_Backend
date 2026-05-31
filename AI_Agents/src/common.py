"""Cross-agent utilities shared across modules under ``AI_Agents/src/``.

Self-contained: depends only on the Python standard library. Other modules in
``AI_Agents/src/`` may import from this file freely; this file must not import
from any peer agent module.

The ``app/`` layer re-exports from here (via ``app/services/ai_bridge/common.py``)
so there is exactly one source of truth for these helpers.
"""

from __future__ import annotations

from typing import Any

_ONE_LAKH = 100_000.0
_ONE_CRORE = 10_000_000.0


# Canonical source for risk-category names. The app layer (app/models/profile/
# risk_profile.RISK_CATEGORIES) re-imports from here via the sys.path hook in
# app/__init__.py — no parallel copy to keep in sync.
#
# Bands are midpoints of the legacy risk_level → willingness conversion
# (risk_willingness = 1.0 + risk_level * 9.0 / 4.0): anchors are
# 1.0 / 3.25 / 5.5 / 7.75 / 10.0, so midpoints fall at 2.125, 4.375,
# 6.625, 8.875.
RISK_CATEGORIES = (
    "Conservative",
    "Moderately Conservative",
    "Moderate",
    "Moderately Aggressive",
    "Aggressive",
)


def category_for_effective_risk_score(score: float) -> str:
    """Map a 1.0-10.0 effective risk score to one of the five categories."""
    if score < 2.125:
        return RISK_CATEGORIES[0]
    if score < 4.375:
        return RISK_CATEGORIES[1]
    if score < 6.625:
        return RISK_CATEGORIES[2]
    if score < 8.875:
        return RISK_CATEGORIES[3]
    return RISK_CATEGORIES[4]


def format_inr_indian(amount: Any) -> str | None:
    """Format a rupee amount in Indian notation (₹X.XX lakh / ₹X.XX crore).

    Pre-computed deterministically so customer-facing LLMs never have to convert
    raw rupees — Haiku frequently drops an order of magnitude (e.g., writes
    "22.6 lakh" for ₹2.26 crore). Callers that pass rupee values to LLMs should
    pair every ``*_inr`` field with a sibling ``*_indian`` string built here,
    and instruct the LLM to copy the ``_indian`` string verbatim.
    """
    if amount is None:
        return None
    try:
        val = float(amount)
    except (TypeError, ValueError):
        return None
    if val == 0:
        return "₹0"
    sign = "-" if val < 0 else ""
    val = abs(val)
    if val < _ONE_LAKH:
        return f"{sign}₹{int(round(val)):,}"
    if val < _ONE_CRORE:
        s = f"{val / _ONE_LAKH:.2f}".rstrip("0").rstrip(".")
        return f"{sign}₹{s} lakh"
    s = f"{val / _ONE_CRORE:.2f}".rstrip("0").rstrip(".")
    return f"{sign}₹{s} crore"
