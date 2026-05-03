"""CRUD for ``mf_fund_metadata`` (global scheme catalog)."""

from __future__ import annotations

import uuid
from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.mf import MfFundMetadata
from app.schemas.mf import MfFundMetadataCreate, MfFundMetadataUpdate
from app.services.mf.paging import clamp_skip_limit


async def list_metadata(
    db: AsyncSession,
    *,
    skip: int = 0,
    limit: int = 50,
    scheme_code: Optional[str] = None,
    category: Optional[str] = None,
) -> list[MfFundMetadata]:
    skip, limit = clamp_skip_limit(skip, limit)
    stmt = select(MfFundMetadata).order_by(MfFundMetadata.scheme_code)
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
