"""CRUD for ``mf_fund_ratings`` (curated rating + dynamic data per scheme)."""

from __future__ import annotations

import uuid
from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.mf import MfFundMetadata, MfFundRating
from app.schemas.mf import MfFundRatingCreate, MfFundRatingUpdate


async def get_rating(db: AsyncSession, rating_id: uuid.UUID) -> MfFundRating:
    row = (
        await db.execute(select(MfFundRating).where(MfFundRating.id == rating_id))
    ).scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Fund rating not found")
    return row


async def get_rating_by_scheme_code(db: AsyncSession, scheme_code: str) -> Optional[MfFundRating]:
    return (
        await db.execute(select(MfFundRating).where(MfFundRating.scheme_code == scheme_code))
    ).scalar_one_or_none()


async def get_rating_by_isin(db: AsyncSession, isin: str) -> Optional[MfFundRating]:
    return (
        await db.execute(select(MfFundRating).where(MfFundRating.isin == isin))
    ).scalar_one_or_none()


async def create_rating(db: AsyncSession, payload: MfFundRatingCreate) -> MfFundRating:
    meta = (
        await db.execute(
            select(MfFundMetadata).where(MfFundMetadata.scheme_code == payload.scheme_code)
        )
    ).scalar_one_or_none()
    if not meta:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No fund metadata for scheme_code={payload.scheme_code}",
        )

    data = payload.model_dump()
    if data.get("isin") is None and meta.isin is not None:
        data["isin"] = meta.isin

    row = MfFundRating(**data)
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


async def update_rating(
    db: AsyncSession, rating_id: uuid.UUID, payload: MfFundRatingUpdate
) -> MfFundRating:
    row = await get_rating(db, rating_id)
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(row, k, v)
    await db.commit()
    await db.refresh(row)
    return row


async def delete_rating(db: AsyncSession, rating_id: uuid.UUID) -> None:
    row = await get_rating(db, rating_id)
    await db.delete(row)
    await db.commit()
