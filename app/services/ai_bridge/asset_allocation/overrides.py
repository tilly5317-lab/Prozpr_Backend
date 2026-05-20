"""Per-turn chat override helpers.

This module exists as a leaf — neither `chat.py` nor `input_builder.py`
imports the other, but both import from here. Replaces the legacy
User._chat_*_override monkey-patch (PR 1).
"""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.services.chat_core.turn_context import TurnContext


_ALLOWED_OVERRIDE_KEYS = frozenset({
    "effective_risk_score",
    "total_corpus",
    "additional_cash_inr",
    "annual_income",
    "monthly_household_expense",
    "emergency_fund_needed",
    "tax_regime",
})


def with_chat_overrides(
    ctx: TurnContext, overrides: dict[str, Any] | None,
) -> TurnContext:
    """Return a new TurnContext with chat_overrides set. The original is unchanged."""
    return dataclasses.replace(ctx, chat_overrides=overrides or None)


def effective_param(ctx: TurnContext, key: str, fallback: Any) -> Any:
    """Return chat_overrides[key] if present, else fallback. Unknown key → ValueError."""
    if key not in _ALLOWED_OVERRIDE_KEYS:
        raise ValueError(f"effective_param: unknown override key {key!r}")
    if ctx.chat_overrides is None:
        return fallback
    if key not in ctx.chat_overrides:
        return fallback
    return ctx.chat_overrides[key]
