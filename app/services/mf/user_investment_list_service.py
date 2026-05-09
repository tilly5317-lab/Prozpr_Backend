"""CRUD for ``user_investment_lists`` (scoped by user; one row per list_kind)."""

from __future__ import annotations

import uuid

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.mf import UserInvestmentList
from app.models.mf.enums import UserInvestmentListKind
from app.schemas.mf import UserInvestmentListCreate, UserInvestmentListUpdate


async def list_lists(db: AsyncSession, user_id: uuid.UUID) -> list[UserInvestmentList]:
    stmt = select(UserInvestmentList).where(UserInvestmentList.user_id == user_id).order_by(UserInvestmentList.list_kind)
    return list((await db.execute(stmt)).scalars().all())


async def get_list_by_kind(
    db: AsyncSession, user_id: uuid.UUID, list_kind: UserInvestmentListKind
) -> UserInvestmentList | None:
    return (
        await db.execute(
            select(UserInvestmentList).where(
                UserInvestmentList.user_id == user_id,
                UserInvestmentList.list_kind == list_kind,
            )
        )
    ).scalar_one_or_none()


async def get_list(db: AsyncSession, list_id: uuid.UUID, user_id: uuid.UUID) -> UserInvestmentList:
    row = (
        await db.execute(
            select(UserInvestmentList).where(
                UserInvestmentList.id == list_id,
                UserInvestmentList.user_id == user_id,
            )
        )
    ).scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Investment list not found")
    return row


async def create_list(db: AsyncSession, user_id: uuid.UUID, payload: UserInvestmentListCreate) -> UserInvestmentList:
    data = payload.model_dump()
    data["user_id"] = user_id
    row = UserInvestmentList(**data)
    db.add(row)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A list for this kind already exists for this user",
        ) from exc
    await db.refresh(row)
    return row


async def update_list(
    db: AsyncSession, list_id: uuid.UUID, user_id: uuid.UUID, payload: UserInvestmentListUpdate
) -> UserInvestmentList:
    row = await get_list(db, list_id, user_id)
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(row, k, v)
    await db.commit()
    await db.refresh(row)
    return row


async def delete_list(db: AsyncSession, list_id: uuid.UUID, user_id: uuid.UUID) -> None:
    row = await get_list(db, list_id, user_id)
    await db.delete(row)
    await db.commit()
