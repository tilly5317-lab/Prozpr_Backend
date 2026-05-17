"""CRUD for ``mf_sip_mandates`` (scoped by user)."""

from __future__ import annotations

import uuid

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.mf import MfSipMandate
from app.schemas.mf import MfSipMandateCreate, MfSipMandateUpdate
from app.services.mf.paging import clamp_skip_limit


async def list_mandates(
    db: AsyncSession, user_id: uuid.UUID, *, skip: int = 0, limit: int = 50
) -> list[MfSipMandate]:
    skip, limit = clamp_skip_limit(skip, limit)
    stmt = (
        select(MfSipMandate)
        .where(MfSipMandate.user_id == user_id)
        .order_by(MfSipMandate.created_at.desc())
        .offset(skip)
        .limit(limit)
    )
    return list((await db.execute(stmt)).scalars().all())


async def get_mandate(db: AsyncSession, mandate_id: uuid.UUID, user_id: uuid.UUID) -> MfSipMandate:
    row = (
        await db.execute(
            select(MfSipMandate).where(MfSipMandate.id == mandate_id, MfSipMandate.user_id == user_id)
        )
    ).scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="SIP mandate not found")
    return row


async def create_mandate(db: AsyncSession, user_id: uuid.UUID, payload: MfSipMandateCreate) -> MfSipMandate:
    data = payload.model_dump()
    data["user_id"] = user_id
    row = MfSipMandate(**data)
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


async def update_mandate(
    db: AsyncSession, mandate_id: uuid.UUID, user_id: uuid.UUID, payload: MfSipMandateUpdate
) -> MfSipMandate:
    row = await get_mandate(db, mandate_id, user_id)
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(row, k, v)
    await db.commit()
    await db.refresh(row)
    return row


async def delete_mandate(db: AsyncSession, mandate_id: uuid.UUID, user_id: uuid.UUID) -> None:
    row = await get_mandate(db, mandate_id, user_id)
    await db.delete(row)
    await db.commit()
