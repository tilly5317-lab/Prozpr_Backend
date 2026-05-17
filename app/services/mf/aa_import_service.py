"""CRUD for AA import batches and nested summary / transaction rows."""

from __future__ import annotations

import uuid

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.mf import MfAaImport, MfAaSummary, MfAaTransaction
from app.schemas.mf import (
    MfAaImportCreate,
    MfAaImportUpdate,
    MfAaSummaryCreate,
    MfAaSummaryUpdate,
    MfAaTransactionCreate,
    MfAaTransactionUpdate,
)
from app.services.mf.aa_access import get_aa_import_for_user
from app.services.mf.paging import clamp_skip_limit


async def list_imports(
    db: AsyncSession, user_id: uuid.UUID, *, skip: int = 0, limit: int = 50
) -> list[MfAaImport]:
    skip, limit = clamp_skip_limit(skip, limit)
    stmt = (
        select(MfAaImport)
        .where(MfAaImport.user_id == user_id)
        .order_by(MfAaImport.imported_at.desc())
        .offset(skip)
        .limit(limit)
    )
    return list((await db.execute(stmt)).scalars().all())


async def get_import(db: AsyncSession, import_id: uuid.UUID, user_id: uuid.UUID) -> MfAaImport:
    return await get_aa_import_for_user(db, import_id, user_id)


async def create_import(db: AsyncSession, user_id: uuid.UUID, payload: MfAaImportCreate) -> MfAaImport:
    data = payload.model_dump()
    data["user_id"] = user_id
    row = MfAaImport(**data)
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


async def update_import(
    db: AsyncSession, import_id: uuid.UUID, user_id: uuid.UUID, payload: MfAaImportUpdate
) -> MfAaImport:
    row = await get_import(db, import_id, user_id)
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(row, k, v)
    await db.commit()
    await db.refresh(row)
    return row


async def delete_import(db: AsyncSession, import_id: uuid.UUID, user_id: uuid.UUID) -> None:
    row = await get_import(db, import_id, user_id)
    await db.delete(row)
    await db.commit()


# --- Summaries ---


async def list_summaries(
    db: AsyncSession, import_id: uuid.UUID, user_id: uuid.UUID, *, skip: int = 0, limit: int = 50
) -> list[MfAaSummary]:
    await get_import(db, import_id, user_id)
    skip, limit = clamp_skip_limit(skip, limit)
    stmt = (
        select(MfAaSummary)
        .where(MfAaSummary.aa_import_id == import_id)
        .order_by(MfAaSummary.row_no)
        .offset(skip)
        .limit(limit)
    )
    return list((await db.execute(stmt)).scalars().all())


async def get_summary(
    db: AsyncSession, import_id: uuid.UUID, summary_id: uuid.UUID, user_id: uuid.UUID
) -> MfAaSummary:
    await get_import(db, import_id, user_id)
    row = (
        await db.execute(
            select(MfAaSummary).where(
                MfAaSummary.id == summary_id,
                MfAaSummary.aa_import_id == import_id,
            )
        )
    ).scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="AA summary row not found")
    return row


async def create_summary(
    db: AsyncSession, import_id: uuid.UUID, user_id: uuid.UUID, payload: MfAaSummaryCreate
) -> MfAaSummary:
    await get_import(db, import_id, user_id)
    data = payload.model_dump()
    data["aa_import_id"] = import_id
    row = MfAaSummary(**data)
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


async def update_summary(
    db: AsyncSession,
    import_id: uuid.UUID,
    summary_id: uuid.UUID,
    user_id: uuid.UUID,
    payload: MfAaSummaryUpdate,
) -> MfAaSummary:
    row = await get_summary(db, import_id, summary_id, user_id)
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(row, k, v)
    await db.commit()
    await db.refresh(row)
    return row


async def delete_summary(
    db: AsyncSession, import_id: uuid.UUID, summary_id: uuid.UUID, user_id: uuid.UUID
) -> None:
    row = await get_summary(db, import_id, summary_id, user_id)
    await db.delete(row)
    await db.commit()


# --- AA raw transactions ---


async def list_aa_transactions(
    db: AsyncSession, import_id: uuid.UUID, user_id: uuid.UUID, *, skip: int = 0, limit: int = 50
) -> list[MfAaTransaction]:
    await get_import(db, import_id, user_id)
    skip, limit = clamp_skip_limit(skip, limit)
    stmt = (
        select(MfAaTransaction)
        .where(MfAaTransaction.aa_import_id == import_id)
        .order_by(MfAaTransaction.row_no)
        .offset(skip)
        .limit(limit)
    )
    return list((await db.execute(stmt)).scalars().all())


async def get_aa_transaction(
    db: AsyncSession, import_id: uuid.UUID, txn_id: uuid.UUID, user_id: uuid.UUID
) -> MfAaTransaction:
    await get_import(db, import_id, user_id)
    row = (
        await db.execute(
            select(MfAaTransaction).where(
                MfAaTransaction.id == txn_id,
                MfAaTransaction.aa_import_id == import_id,
            )
        )
    ).scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="AA transaction row not found")
    return row


async def create_aa_transaction(
    db: AsyncSession, import_id: uuid.UUID, user_id: uuid.UUID, payload: MfAaTransactionCreate
) -> MfAaTransaction:
    await get_import(db, import_id, user_id)
    data = payload.model_dump()
    data["aa_import_id"] = import_id
    row = MfAaTransaction(**data)
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


async def update_aa_transaction(
    db: AsyncSession,
    import_id: uuid.UUID,
    txn_id: uuid.UUID,
    user_id: uuid.UUID,
    payload: MfAaTransactionUpdate,
) -> MfAaTransaction:
    row = await get_aa_transaction(db, import_id, txn_id, user_id)
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(row, k, v)
    await db.commit()
    await db.refresh(row)
    return row


async def delete_aa_transaction(
    db: AsyncSession, import_id: uuid.UUID, txn_id: uuid.UUID, user_id: uuid.UUID
) -> None:
    row = await get_aa_transaction(db, import_id, txn_id, user_id)
    await db.delete(row)
    await db.commit()
