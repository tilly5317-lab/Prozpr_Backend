"""CRUD for ``mf_fund_metadata`` (global scheme catalog)."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

from fastapi import HTTPException, status
from sqlalchemy import exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.mf import MfFundMetadata, MfNavHistory
from app.schemas.mf import MfFundMetadataCreate, MfFundMetadataUpdate
from app.services.mf.paging import clamp_skip_limit

# Only expose schemes in list/search when NAV feed has at least one row in this window.
_RECENT_NAV_LOOKBACK_DAYS = 30


def _has_recent_nav():
    cutoff = (datetime.now(timezone.utc) - timedelta(days=_RECENT_NAV_LOOKBACK_DAYS)).date()
    # Correlate so `mf_fund_metadata` in the subquery refers to the outer row only (not a separate scan).
    inner = (
        select(MfNavHistory.id)
        .where(
            MfNavHistory.scheme_code == MfFundMetadata.scheme_code,
            MfNavHistory.nav_date >= cutoff,
        )
        .correlate(MfFundMetadata)
    )
    return exists(inner)


async def list_metadata(
    db: AsyncSession,
    *,
    skip: int = 0,
    limit: int = 50,
    scheme_code: Optional[str] = None,
    category: Optional[str] = None,
) -> list[MfFundMetadata]:
    skip, limit = clamp_skip_limit(skip, limit)
    stmt = (
        select(MfFundMetadata)
        .where(_has_recent_nav())
        .order_by(MfFundMetadata.scheme_code)
    )
    if scheme_code:
        stmt = stmt.where(MfFundMetadata.scheme_code == scheme_code)
    if category:
        stmt = stmt.where(MfFundMetadata.category == category)
    stmt = stmt.offset(skip).limit(limit)
    return list((await db.execute(stmt)).scalars().all())


async def get_metadata(db: AsyncSession, metadata_id: uuid.UUID) -> MfFundMetadata:
    row = (await db.execute(select(MfFundMetadata).where(MfFundMetadata.id == metadata_id))).scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Fund metadata not found")
    return row


async def create_metadata(db: AsyncSession, payload: MfFundMetadataCreate) -> MfFundMetadata:
    data = payload.model_dump()
    row = MfFundMetadata(**data)
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


async def update_metadata(db: AsyncSession, metadata_id: uuid.UUID, payload: MfFundMetadataUpdate) -> MfFundMetadata:
    row = await get_metadata(db, metadata_id)
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(row, k, v)
    await db.commit()
    await db.refresh(row)
    return row


async def delete_metadata(db: AsyncSession, metadata_id: uuid.UUID) -> None:
    row = await get_metadata(db, metadata_id)
    await db.delete(row)
    await db.commit()


async def search_metadata(
    db: AsyncSession,
    *,
    q: Optional[str] = None,
    category: Optional[str] = None,
    sub_category: Optional[str] = None,
    asset_class: Optional[str] = None,
    amc_name: Optional[str] = None,
    active_only: bool = True,
    limit: int = 20,
    offset: int = 0,
) -> Tuple[list[MfFundMetadata], int]:
    # Bounded paging — prevents unbounded scans from the public-ish search endpoint.
    offset, limit = clamp_skip_limit(offset, limit)
    if limit > 50:
        limit = 50

    stmt = select(MfFundMetadata)
    count_stmt = select(func.count(MfFundMetadata.id))

    conditions = [_has_recent_nav()]
    if active_only:
        conditions.append(MfFundMetadata.is_active.is_(True))

    if q:
        token = q.strip()
        if token:
            pattern = f"%{token}%"
            conditions.append(
                or_(
                    MfFundMetadata.scheme_name.ilike(pattern),
                    MfFundMetadata.amc_name.ilike(pattern),
                    MfFundMetadata.scheme_code.ilike(pattern),
                    MfFundMetadata.isin.ilike(pattern),
                )
            )

    if category:
        conditions.append(MfFundMetadata.category == category)
    if sub_category:
        conditions.append(MfFundMetadata.sub_category == sub_category)
    if asset_class:
        conditions.append(MfFundMetadata.asset_class == asset_class)
    if amc_name:
        conditions.append(MfFundMetadata.amc_name == amc_name)

    for cond in conditions:
        stmt = stmt.where(cond)
        count_stmt = count_stmt.where(cond)

    stmt = stmt.order_by(MfFundMetadata.scheme_name).offset(offset).limit(limit)

    rows = list((await db.execute(stmt)).scalars().all())
    total = (await db.execute(count_stmt)).scalar() or 0
    return rows, int(total)
