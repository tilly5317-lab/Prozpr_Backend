"""CRUD for ``portfolio_allocation_snapshots`` (scoped by user)."""

from __future__ import annotations

import uuid
from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.mf import PortfolioAllocationSnapshot
from app.models.mf.enums import PortfolioSnapshotKind
from app.schemas.mf import (
    PortfolioAllocationSnapshotCreate,
    PortfolioAllocationSnapshotUpdate,
)
from app.services.mf.paging import clamp_skip_limit


async def list_snapshots(
    db: AsyncSession,
    user_id: uuid.UUID,
    *,
    skip: int = 0,
    limit: int = 50,
    snapshot_kind: Optional[PortfolioSnapshotKind] = None,
) -> list[PortfolioAllocationSnapshot]:
    skip, limit = clamp_skip_limit(skip, limit)
    stmt = (
        select(PortfolioAllocationSnapshot)
        .where(PortfolioAllocationSnapshot.user_id == user_id)
        .order_by(PortfolioAllocationSnapshot.effective_at.desc())
    )
    if snapshot_kind:
        stmt = stmt.where(PortfolioAllocationSnapshot.snapshot_kind == snapshot_kind)
    stmt = stmt.offset(skip).limit(limit)
    return list((await db.execute(stmt)).scalars().all())


async def get_snapshot(db: AsyncSession, snapshot_id: uuid.UUID, user_id: uuid.UUID) -> PortfolioAllocationSnapshot:
    row = (
        await db.execute(
            select(PortfolioAllocationSnapshot).where(
                PortfolioAllocationSnapshot.id == snapshot_id,
                PortfolioAllocationSnapshot.user_id == user_id,
            )
        )
    ).scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Allocation snapshot not found")
    return row


async def create_snapshot(
    db: AsyncSession, user_id: uuid.UUID, payload: PortfolioAllocationSnapshotCreate
) -> PortfolioAllocationSnapshot:
    data = payload.model_dump(exclude_unset=True, exclude_none=True)
    data["user_id"] = user_id
    row = PortfolioAllocationSnapshot(**data)
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


async def update_snapshot(
    db: AsyncSession, snapshot_id: uuid.UUID, user_id: uuid.UUID, payload: PortfolioAllocationSnapshotUpdate
) -> PortfolioAllocationSnapshot:
    row = await get_snapshot(db, snapshot_id, user_id)
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(row, k, v)
    await db.commit()
    await db.refresh(row)
    return row


async def delete_snapshot(db: AsyncSession, snapshot_id: uuid.UUID, user_id: uuid.UUID) -> None:
    row = await get_snapshot(db, snapshot_id, user_id)
    await db.delete(row)
    await db.commit()
