"""Insert ``asset_allocation_aggregate`` rows (planned + actual run-level roll-up).

Two rows per run: one ``planned`` (pre-guardrail) and one ``actual``
(post-guardrail), capturing the equity / debt / others totals and percentages.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset_allocation.bucket import (
    AssetAllocationAggregate,
    AssetClassSplitKind,
)


def _float(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


async def insert_asset_allocation_aggregates(
    db: AsyncSession,
    *,
    run_id: uuid.UUID,
    user_id: uuid.UUID,
    doc: dict[str, Any],
) -> None:
    """Insert planned + actual aggregate rows from ``doc["asset_class_breakdown"]``."""
    breakdown = doc.get("asset_class_breakdown") or {}

    for key, split_kind in [
        ("planned", AssetClassSplitKind.planned),
        ("actual", AssetClassSplitKind.actual),
    ]:
        block = breakdown.get(key) or {}
        if not isinstance(block, dict):
            continue

        db.add(
            AssetAllocationAggregate(
                run_id=run_id,
                user_id=user_id,
                split_kind=split_kind,
                equity_amount=_float(block.get("equity_total")),
                debt_amount=_float(block.get("debt_total")),
                others_amount=_float(block.get("others_total")),
                equity_pct=_float(block.get("equity_total_pct")),
                debt_pct=_float(block.get("debt_total_pct")),
                others_pct=_float(block.get("others_total_pct")),
            )
        )

    await db.flush()
