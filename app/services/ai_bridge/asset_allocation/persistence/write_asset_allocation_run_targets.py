"""Insert ``asset_allocation_run_targets`` rows (per-run goal snapshots)."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset_allocation.run import AssetAllocationRun, AssetAllocationRunTarget


def _float(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


async def insert_asset_allocation_run_targets_for_run(
    db: AsyncSession,
    run: AssetAllocationRun,
    doc: dict[str, Any],
    *,
    financial_goal_ids_by_name: dict[str, uuid.UUID] | None,
) -> dict[str, uuid.UUID]:
    """Insert per-run target rows.

    Returns ``{ goal_name: AssetAllocationRunTarget.id }`` for downstream
    bucket-to-target linking.
    """
    goals = (doc.get("client_summary") or {}).get("goals") or []
    goal_id_map = financial_goal_ids_by_name or {}
    name_to_id: dict[str, uuid.UUID] = {}

    for goal in goals:
        if not isinstance(goal, dict):
            continue
        name = str(goal.get("goal_name") or "").strip()
        if not name:
            continue

        row = AssetAllocationRunTarget(
            run_id=run.id,
            financial_goal_id=goal_id_map.get(name),
            goal_name=name[:150],
            time_to_goal_months=int(goal.get("time_to_goal_months") or 0),
            amount_needed=_float(goal.get("amount_needed")),
            goal_priority=str(goal.get("goal_priority") or "negotiable")[:40],
            investment_goal=str(goal.get("investment_goal") or "wealth_creation")[:60],
        )
        db.add(row)
        await db.flush()
        name_to_id[name] = row.id

    return name_to_id
