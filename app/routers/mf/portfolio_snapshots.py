"""HTTP routes for ``portfolio_allocation_snapshots``."""

from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import CurrentUser, get_effective_user
from app.models.mf.enums import PortfolioSnapshotKind
from app.schemas.mf import (
    PortfolioAllocationSnapshotCreate,
    PortfolioAllocationSnapshotResponse,
    PortfolioAllocationSnapshotUpdate,
)
from app.services.mf import portfolio_snapshot_service

router = APIRouter(prefix="/portfolio-allocation-snapshots", tags=["MF Data"])


@router.get("/", response_model=list[PortfolioAllocationSnapshotResponse])
async def list_portfolio_snapshots(
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    snapshot_kind: Optional[PortfolioSnapshotKind] = Query(None),
):
    return await portfolio_snapshot_service.list_snapshots(
        db, current_user.id, skip=skip, limit=limit, snapshot_kind=snapshot_kind
    )


@router.get("/{snapshot_id}", response_model=PortfolioAllocationSnapshotResponse)
async def get_portfolio_snapshot(
    snapshot_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    return await portfolio_snapshot_service.get_snapshot(db, snapshot_id, current_user.id)


@router.post("/", response_model=PortfolioAllocationSnapshotResponse, status_code=status.HTTP_201_CREATED)
async def create_portfolio_snapshot(
    payload: PortfolioAllocationSnapshotCreate,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    return await portfolio_snapshot_service.create_snapshot(db, current_user.id, payload)


@router.patch("/{snapshot_id}", response_model=PortfolioAllocationSnapshotResponse)
async def update_portfolio_snapshot(
    snapshot_id: uuid.UUID,
    payload: PortfolioAllocationSnapshotUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    return await portfolio_snapshot_service.update_snapshot(db, snapshot_id, current_user.id, payload)


@router.delete("/{snapshot_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_portfolio_snapshot(
    snapshot_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    await portfolio_snapshot_service.delete_snapshot(db, snapshot_id, current_user.id)
