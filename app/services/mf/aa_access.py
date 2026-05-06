"""Ownership checks for account-aggregator import trees."""

from __future__ import annotations

import uuid

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.mf import MfAaImport


async def get_aa_import_for_user(
    db: AsyncSession, import_id: uuid.UUID, user_id: uuid.UUID
) -> MfAaImport:
    result = await db.execute(select(MfAaImport).where(MfAaImport.id == import_id))
    row = result.scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="AA import not found")
    if row.user_id != user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not allowed for this import")
    return row
