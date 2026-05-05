"""Per-turn chat override helpers for rebalancing.

Mirrors asset_allocation/overrides.py with rebalancing's allow-list. Re-imports
`with_chat_overrides` from AA — the helper is generic (just dataclasses.replace
on TurnContext.chat_overrides), no need to duplicate.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.services.ai_bridge.asset_allocation.overrides import (  # re-imported helper
    with_chat_overrides,
)

if TYPE_CHECKING:
    from app.services.chat_core.turn_context import TurnContext


_REBAL_ALLOWED_OVERRIDE_KEYS = frozenset({
    "effective_tax_rate",
    "stcg_offset_budget_inr",
    "carryforward_st_loss_inr",
    "carryforward_lt_loss_inr",
    "additional_cash_inr",  # cross-module: also in AA's allow-list (corpus adjustment)
})


def effective_param(ctx: "TurnContext", key: str, fallback: Any) -> Any:
    """Return chat_overrides[key] if present, else fallback. Unknown key → ValueError."""
    if key not in _REBAL_ALLOWED_OVERRIDE_KEYS:
        raise ValueError(f"effective_param: unknown override key {key!r}")
    if ctx.chat_overrides is None:
        return fallback
    if key not in ctx.chat_overrides:
        return fallback
    return ctx.chat_overrides[key]
