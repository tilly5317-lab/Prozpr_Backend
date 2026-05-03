"""CRUD for ``mf_transactions`` (scoped by user)."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.mf import MfSipMandate, MfTransaction
from app.schemas.mf import MfTransactionCreate, MfTransactionUpdate
from app.services.mf.paging import clamp_skip_limit


async def _ensure_sip_owned(
    db: AsyncSession, sip_id: uuid.UUID | None, user_id: uuid.UUID
) -> None:
    if sip_id is None:
        return
    row = (
        await db.execute(select(MfSipMandate).where(MfSipMandate.id == sip_id, MfSipMandate.user_id == user_id))
    ).scalar_one_or_none()
    if not row:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="sip_mandate_id does not belong to this user"
        )


async def list_transactions(
    db: AsyncSession,
    user_id: uuid.UUID,
    *,
    skip: int = 0,
    limit: int = 50,
    scheme_code: Optional[str] = None,
    transaction_date_from: Optional[date] = None,
    transaction_date_to: Optional[date] = None,
) -> list[MfTransaction]:
    skip, limit = clamp_skip_limit(skip, limit)
    stmt = (
        select(MfTransaction)
        .where(MfTransaction.user_id == user_id)
        .order_by(MfTransaction.transaction_date.desc())
    )
    if scheme_code:
        stmt = stmt.where(MfTransaction.scheme_code == scheme_code)
    if transaction_date_from:
        stmt = stmt.where(MfTransaction.transaction_date >= transaction_date_from)
    if transaction_date_to:
        stmt = stmt.where(MfTransaction.transaction_date <= transaction_date_to)
    stmt = stmt.offset(skip).limit(limit)
    return list((await db.execute(stmt)).scalars().all())


async def get_transaction(db: AsyncSession, txn_id: uuid.UUID, user_id: uuid.UUID) -> MfTransaction:
    row = (
        await db.execute(
            select(MfTransaction).where(MfTransaction.id == txn_id, MfTransaction.user_id == user_id)
        )
    ).scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="MF transaction not found")
    return row


async def create_transaction(db: AsyncSession, user_id: uuid.UUID, payload: MfTransactionCreate) -> MfTransaction:
    await _ensure_sip_owned(db, payload.sip_mandate_id, user_id)
    data = payload.model_dump()
    data["user_id"] = user_id
    row = MfTransaction(**data)
    db.add(row)
    try:
        await db.commit()
    except Exception as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Could not create transaction (duplicate fingerprint?): {exc}",
        ) from exc
    await db.refresh(row)
    return row


async def update_transaction(
    db: AsyncSession, txn_id: uuid.UUID, user_id: uuid.UUID, payload: MfTransactionUpdate
) -> MfTransaction:
    row = await get_transaction(db, txn_id, user_id)
    data = payload.model_dump(exclude_unset=True)
    if "sip_mandate_id" in data:
        await _ensure_sip_owned(db, data["sip_mandate_id"], user_id)
    for k, v in data.items():
        setattr(row, k, v)
    try:
        await db.commit()
    except Exception as exc:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    await db.refresh(row)
    return row


async def delete_transaction(db: AsyncSession, txn_id: uuid.UUID, user_id: uuid.UUID) -> None:
    row = await get_transaction(db, txn_id, user_id)
    await db.delete(row)
    await db.commit()
