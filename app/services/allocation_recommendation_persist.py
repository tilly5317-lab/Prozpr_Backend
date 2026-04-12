"""Persist Ideal_asset_allocation outputs for rebalancing UI and portfolio snapshots."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.mf.enums import PortfolioSnapshotKind
from app.models.mf.portfolio_allocation_snapshot import PortfolioAllocationSnapshot
from app.models.rebalancing import RebalancingRecommendation, RebalancingStatus
from app.services.ai_bridge.common import ensure_ai_agents_path
from app.services.portfolio_service import get_or_create_primary_portfolio

ensure_ai_agents_path()

from Ideal_asset_allocation.models import AllocationOutput


def _allocation_output_to_jsonable(output: AllocationOutput) -> dict[str, Any]:
    return output.model_dump(mode="json")


async def persist_ideal_allocation_recommendation(
    db: AsyncSession,
    user_id: uuid.UUID,
    output: AllocationOutput,
    *,
    chat_session_id: uuid.UUID | None = None,
    user_question: str | None = None,
    spine_mode: str | None = None,
) -> tuple[uuid.UUID, uuid.UUID]:
    """
    Store a pending ``RebalancingRecommendation`` (full JSON) and an ``IDEAL``
    ``PortfolioAllocationSnapshot`` for charts / detail views.

    Returns ``(rebalancing_recommendation_id, portfolio_allocation_snapshot_id)``.
    """
    portfolio = await get_or_create_primary_portfolio(db, user_id)
    payload = _allocation_output_to_jsonable(output)

    rec = RebalancingRecommendation(
        portfolio_id=portfolio.id,
        status=RebalancingStatus.pending,
        recommendation_data={
            "source": "ideal_asset_allocation",
            "ideal_allocation_output": payload,
            "chat_session_id": str(chat_session_id) if chat_session_id else None,
            "user_question": user_question,
            "spine_mode": spine_mode,
        },
        reason="Ideal mutual fund allocation (AI pipeline)",
    )
    db.add(rec)

    ac = output.asset_class_allocation
    snapshot_allocation: dict[str, Any] = {
        "rows": [
            {"asset_class": "Equity", "weight_pct": float(ac.equities.pct)},
            {"asset_class": "Debt", "weight_pct": float(ac.debt.pct)},
            {"asset_class": "Others", "weight_pct": float(ac.others.pct)},
        ],
        "equity_pct": ac.equities.pct,
        "debt_pct": ac.debt.pct,
        "others_pct": ac.others.pct,
        "ideal_allocation_output": payload,
    }

    snap = PortfolioAllocationSnapshot(
        user_id=user_id,
        snapshot_kind=PortfolioSnapshotKind.IDEAL,
        allocation=snapshot_allocation,
        source="ideal_asset_allocation",
        notes=(user_question or "")[:2000] or None,
    )
    db.add(snap)

    await db.flush()
    return rec.id, snap.id
