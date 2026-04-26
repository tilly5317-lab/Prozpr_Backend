"""Chart tool — target vs actual allocation bars.

Reads the user's most recent IDEAL allocation snapshot (target) and the
current PortfolioAllocation rows (actual), pairs them by asset class, and
returns a TargetVsActualBars payload. Returns None when either side is
missing — the chart is meaningless without both.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.mf.enums import PortfolioSnapshotKind
from app.models.mf.portfolio_allocation_snapshot import PortfolioAllocationSnapshot
from app.models.portfolio import PortfolioAllocation
from app.services.portfolio_service import get_primary_portfolio
from app.services.visualization_tools.schema import (
    TargetVsActualBar,
    TargetVsActualBars,
)


def _extract_target_pcts(snapshot_allocation: dict) -> dict[str, float]:
    """Pull asset-class → percentage from the IDEAL snapshot's JSON blob."""
    rows = snapshot_allocation.get("rows") or []
    out: dict[str, float] = {}
    for row in rows:
        cls = row.get("asset_class")
        pct = row.get("weight_pct")
        if isinstance(cls, str) and isinstance(pct, (int, float)):
            out[cls] = float(pct)
    return out


async def _latest_ideal_targets(
    db: AsyncSession, user_id: uuid.UUID
) -> dict[str, float] | None:
    stmt = (
        select(PortfolioAllocationSnapshot)
        .where(
            PortfolioAllocationSnapshot.user_id == user_id,
            PortfolioAllocationSnapshot.snapshot_kind == PortfolioSnapshotKind.IDEAL,
        )
        .order_by(PortfolioAllocationSnapshot.effective_at.desc())
        .limit(1)
    )
    row = (await db.execute(stmt)).scalar_one_or_none()
    if row is None:
        return None
    targets = _extract_target_pcts(row.allocation or {})
    return targets or None


async def build_target_vs_actual(
    db: AsyncSession, user_id: uuid.UUID
) -> TargetVsActualBars | None:
    targets = await _latest_ideal_targets(db, user_id)
    if not targets:
        return None

    portfolio = await get_primary_portfolio(db, user_id)
    if portfolio is None:
        return None

    actual_stmt = select(PortfolioAllocation).where(
        PortfolioAllocation.portfolio_id == portfolio.id
    )
    actual_rows = (await db.execute(actual_stmt)).scalars().all()
    if not actual_rows:
        return None

    actual: dict[str, float] = {
        r.asset_class: float(r.allocation_percentage) for r in actual_rows
    }

    asset_classes = list(targets.keys())
    for cls in actual.keys():
        if cls not in asset_classes:
            asset_classes.append(cls)

    bars = []
    for cls in asset_classes:
        target_pct = targets.get(cls, 0.0)
        actual_pct = actual.get(cls, 0.0)
        bars.append(
            TargetVsActualBar(
                asset_class=cls,
                target_pct=target_pct,
                actual_pct=actual_pct,
                drift_pct=actual_pct - target_pct,
            )
        )

    bars.sort(key=lambda b: max(b.target_pct, b.actual_pct), reverse=True)

    return TargetVsActualBars(
        title="Target vs Actual Allocation",
        bars=bars,
    )
