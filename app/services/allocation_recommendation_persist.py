"""Persist goal-based allocation outputs for rebalancing UI and portfolio snapshots."""

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

from goal_based_allocation_pydantic.models import GoalAllocationOutput


def _allocation_output_to_jsonable(output: GoalAllocationOutput) -> dict[str, Any]:
    payload = output.model_dump(mode="json")
    rows = payload.get("aggregated_subgroups") or []
    for row_dump, row_obj in zip(rows, output.aggregated_subgroups):
        row_dump["subgroup"] = row_obj.customer_label
    return payload


def _asset_class_pcts_from_subgroups(output: GoalAllocationOutput) -> tuple[float, float, float]:
    """Fallback: derive equity/debt/others percents from aggregated subgroup totals."""
    totals = {"equity": 0.0, "debt": 0.0, "others": 0.0}
    for row in output.aggregated_subgroups:
        fm = row.fund_mapping
        if fm is None:
            continue
        key = fm.asset_class
        if key in totals:
            totals[key] += float(row.total)
    grand = totals["equity"] + totals["debt"] + totals["others"]
    if grand <= 0:
        return 0.0, 0.0, 0.0
    return (
        round(totals["equity"] / grand * 100, 2),
        round(totals["debt"] / grand * 100, 2),
        round(totals["others"] / grand * 100, 2),
    )


async def persist_goal_allocation_recommendation(
    db: AsyncSession,
    user_id: uuid.UUID,
    output: GoalAllocationOutput,
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
            "source": "goal_based_allocation_pydantic",
            "goal_allocation_output": payload,
            "chat_session_id": str(chat_session_id) if chat_session_id else None,
            "user_question": user_question,
            "spine_mode": spine_mode,
        },
        reason="Goal-based mutual fund allocation (AI pipeline)",
    )
    db.add(rec)

    acb = output.asset_class_breakdown
    if acb is not None:
        equity_pct = float(acb.actual.equity_total_pct)
        debt_pct = float(acb.actual.debt_total_pct)
        others_pct = float(acb.actual.others_total_pct)
    else:
        equity_pct, debt_pct, others_pct = _asset_class_pcts_from_subgroups(output)

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
        source="goal_based_allocation_pydantic",
        notes=(user_question or "")[:2000] or None,
    )
    db.add(snap)

    await db.flush()
    return rec.id, snap.id
