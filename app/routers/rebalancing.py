"""FastAPI router — rebalancing run listing, detail, and status update.

Backed by the normalized ``rebalancing_*`` family. List endpoint returns light
rows; detail endpoint eager-loads totals, subgroup summaries, trades, and
warnings so the UI gets one round-trip per run.
"""


from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.dependencies import CurrentUser, get_effective_user
from app.models.rebalancing.rebalancing_run import RebalancingRun, RebalancingRunStatus
from app.schemas.rebalancing import (
    RebalancingRunDetailResponse,
    RebalancingRunListItem,
    RebalancingStatusUpdate,
)

router = APIRouter(prefix="/rebalancing", tags=["Rebalancing"])


@router.get("/", response_model=list[RebalancingRunListItem])
async def list_runs(
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    stmt = (
        select(RebalancingRun)
        .where(RebalancingRun.user_id == current_user.id)
        .order_by(RebalancingRun.created_at.desc())
    )
    rows = (await db.execute(stmt)).scalars().all()
    return [RebalancingRunListItem.model_validate(r) for r in rows]


@router.get("/{run_id}", response_model=RebalancingRunDetailResponse)
async def get_run(
    run_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    stmt = (
        select(RebalancingRun)
        .where(
            RebalancingRun.id == run_id,
            RebalancingRun.user_id == current_user.id,
        )
        .options(
            selectinload(RebalancingRun.totals),
            selectinload(RebalancingRun.subgroup_summaries),
            selectinload(RebalancingRun.trades),
            selectinload(RebalancingRun.warnings),
        )
    )
    run = (await db.execute(stmt)).scalar_one_or_none()
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Rebalancing run not found"
        )
    return RebalancingRunDetailResponse.model_validate(run)


@router.put("/{run_id}/status", response_model=RebalancingRunListItem)
async def update_status(
    run_id: uuid.UUID,
    payload: RebalancingStatusUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    stmt = (
        select(RebalancingRun)
        .where(
            RebalancingRun.id == run_id,
            RebalancingRun.user_id == current_user.id,
        )
    )
    run = (await db.execute(stmt)).scalar_one_or_none()
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Rebalancing run not found"
        )

    run.status = RebalancingRunStatus(payload.status)
    await db.commit()
    await db.refresh(run)
    return RebalancingRunListItem.model_validate(run)
