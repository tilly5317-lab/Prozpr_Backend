"""Persist goal-based allocation outputs for portfolio snapshots.

Creates an ``IDEAL`` ``PortfolioAllocationSnapshot`` with the full
``GoalAllocationOutput`` embedded so it can be re-loaded for cached
rebalancing runs.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.mf.enums import PortfolioSnapshotKind
from app.models.mf.portfolio_allocation_snapshot import PortfolioAllocationSnapshot
from app.services.ai_bridge.common import ensure_ai_agents_path

ensure_ai_agents_path()

from asset_allocation_pydantic.models import GoalAllocationOutput  # noqa: E402


def _allocation_output_to_jsonable(output: GoalAllocationOutput) -> dict[str, Any]:
    return output.model_dump(mode="json")


async def persist_goal_allocation_recommendation(
    db: AsyncSession,
    user_id: uuid.UUID,
    output: GoalAllocationOutput,
    *,
    chat_session_id: uuid.UUID | None = None,
    user_question: str | None = None,
    spine_mode: str | None = None,
) -> tuple[uuid.UUID | None, uuid.UUID]:
    """Store an ``IDEAL`` ``PortfolioAllocationSnapshot`` for charts / detail views.

    Returns ``(None, portfolio_allocation_snapshot_id)``.

    The first element is ``None`` for backward compatibility — callers that
    previously received a ``RebalancingRecommendation`` id should use the
    ``AssetAllocationRun`` id from the normalized persistence path instead.
    """
    payload = _allocation_output_to_jsonable(output)

    acb = output.asset_class_breakdown
    equity_pct = float(acb.recommended.equity_total_pct)
    debt_pct = float(acb.recommended.debt_total_pct)
    others_pct = float(acb.recommended.others_total_pct)

    snapshot_allocation: dict[str, Any] = {
        "rows": [
            {"asset_class": "Equity", "weight_pct": equity_pct},
            {"asset_class": "Debt", "weight_pct": debt_pct},
            {"asset_class": "Others", "weight_pct": others_pct},
        ],
        "equity_pct": equity_pct,
        "debt_pct": debt_pct,
        "others_pct": others_pct,
        "goal_allocation_output": payload,
    }

    snap = PortfolioAllocationSnapshot(
        user_id=user_id,
        snapshot_kind=PortfolioSnapshotKind.IDEAL,
        allocation=snapshot_allocation,
        source="asset_allocation_pydantic",
        notes=(user_question or "")[:2000] or None,
    )
    db.add(snap)

    await db.flush()
    return None, snap.id
