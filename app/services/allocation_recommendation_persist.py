"""Glue layer: persist an asset-allocation pipeline run *and* the portfolio-side
artefacts the rest of the app expects from it.

The actual ``asset_allocation_*`` table writes live in
``app.services.asset_allocation_persist`` — this module only adds the
side-effects that are *not* part of the asset-allocation schema:

* ensures the user has a primary portfolio (so the run can reference it);
* writes the IDEAL ``PortfolioAllocationSnapshot`` the chart layer renders and
  the rebalancing engine reads ``goal_allocation_output`` back out of.

Returns ``(asset_allocation_run_id, portfolio_allocation_snapshot_id)``.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.mf.enums import PortfolioSnapshotKind
from app.models.mf.portfolio_allocation_snapshot import PortfolioAllocationSnapshot
from app.services.ai_bridge.common import ensure_ai_agents_path
from app.services.asset_allocation_persist import persist_asset_allocation_run
from app.services.portfolio_service import get_or_create_primary_portfolio

ensure_ai_agents_path()

from asset_allocation_pydantic.models import (  # type: ignore[import-not-found]  # noqa: E402
    GoalAllocationOutput,
)


async def persist_goal_allocation_recommendation(
    db: AsyncSession,
    user_id: uuid.UUID,
    output: GoalAllocationOutput,
    *,
    input_payload: dict[str, Any] | None = None,
    chat_session_id: uuid.UUID | None = None,
    user_question: str | None = None,
    spine_mode: str | None = None,
) -> tuple[uuid.UUID, uuid.UUID]:
    """Persist the run + an IDEAL snapshot; return ``(run_id, snapshot_id)``."""
    portfolio = await get_or_create_primary_portfolio(db, user_id)

    run_id = await persist_asset_allocation_run(
        db,
        user_id=user_id,
        output=output,
        portfolio_id=portfolio.id,
        chat_session_id=chat_session_id,
        user_question=user_question,
        spine_mode=spine_mode,
        input_payload=input_payload,
    )

    actual_totals = output.asset_class_breakdown.actual
    snapshot_allocation: dict[str, Any] = {
        "rows": [
            {"asset_class": "Equity", "weight_pct": float(actual_totals.equity_total_pct)},
            {"asset_class": "Debt", "weight_pct": float(actual_totals.debt_total_pct)},
            {"asset_class": "Others", "weight_pct": float(actual_totals.others_total_pct)},
        ],
        "equity_pct": float(actual_totals.equity_total_pct),
        "debt_pct": float(actual_totals.debt_total_pct),
        "others_pct": float(actual_totals.others_total_pct),
        "goal_allocation_output": output.model_dump(mode="json"),
        "asset_allocation_run_id": str(run_id),
    }
    snap = PortfolioAllocationSnapshot(
        user_id=user_id,
        snapshot_kind=PortfolioSnapshotKind.IDEAL,
        allocation=snapshot_allocation,
        source=spine_mode or "asset_allocation_pydantic",
        notes=(user_question or "")[:2000] or None,
    )
    db.add(snap)
    await db.flush()
    return run_id, snap.id
