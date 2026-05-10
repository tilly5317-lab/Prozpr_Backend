"""Chart builder — per-fund buy/sell ledger from a rebalancing trade plan.

Reads the engine response's subgroups → actions and emits one row per fund
with its sub-category, buy ₹, and sell ₹. Sorted by absolute trade size
(largest first) so the most consequential trades lead.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from app.services.ai_bridge.common import ensure_ai_agents_path
from app.services.visualization_tools.buy_sell_ledger.schema import (
    BuySellLedger,
    BuySellRow,
)

ensure_ai_agents_path()

from Rebalancing.models import FundRowAfterStep5  # type: ignore[import-not-found]  # noqa: E402, F401


async def build_buy_sell_ledger(response: Any) -> BuySellLedger | None:
    """Build the per-fund buy/sell ledger, or None if no trades."""
    rows: list[BuySellRow] = []
    for subgroup in response.subgroups:
        for action in subgroup.actions:
            buy = float(action.pass1_buy_amount or Decimal(0))
            sell_p1 = float(action.pass1_sell_amount or Decimal(0))
            sell_p2 = float(action.pass2_sell_amount or Decimal(0))
            sell = sell_p1 + sell_p2
            if buy <= 0 and sell <= 0:
                continue
            # Tolerate different fund-identity field names on the engine model.
            name = (
                getattr(action, "recommended_fund", None)
                or getattr(action, "fund_name", None)
                or getattr(action, "name", None)
                or getattr(action, "scheme_name", None)
                or getattr(action, "instrument_name", None)
                or "Unknown fund"
            )
            rows.append(BuySellRow(
                name=str(name),
                sub_category=str(getattr(action, "sub_category", "") or ""),
                buy_inr=buy,
                sell_inr=sell,
            ))
    if not rows:
        return None

    rows.sort(key=lambda r: -(r.buy_inr + r.sell_inr))

    return BuySellLedger(
        title="Trades to execute",
        subtitle="Buy and sell amounts per fund",
        rows=rows,
    )
