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
