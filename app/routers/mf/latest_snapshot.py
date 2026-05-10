"""Routes for user_mf_latest_snapshot holdings table."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import CurrentUser, get_effective_user
from app.schemas.mf import UserMfLatestSnapshotResponse
from app.services.mf.latest_snapshot_service import (
    list_user_latest_snapshot,
    rebuild_user_latest_snapshot,
)

router = APIRouter(prefix="/latest-snapshot", tags=["MF Data"])


@router.get("/", response_model=list[UserMfLatestSnapshotResponse])
async def list_latest_snapshot(
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
):
    return await list_user_latest_snapshot(db, current_user.id, skip=skip, limit=limit)


@router.post("/rebuild", response_model=dict[str, int])
async def rebuild_latest_snapshot(
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_effective_user),
):
    count = await rebuild_user_latest_snapshot(db, current_user.id)
    return {"rows": count}
