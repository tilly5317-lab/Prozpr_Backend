"""CRUD for ``mf_nav_history``."""

from __future__ import annotations

from datetime import date
from typing import Iterable, Optional

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.mf import MfNavHistory
from app.schemas.mf import MfNavHistoryCreate, MfNavHistoryUpdate
from app.services.mf.paging import clamp_skip_limit


async def list_nav_rows(
    db: AsyncSession,
    *,
    skip: int = 0,
    limit: int = 50,
    scheme_code: Optional[str] = None,
    isin: Optional[str] = None,
    nav_date_from: Optional[date] = None,
    nav_date_to: Optional[date] = None,
) -> list[MfNavHistory]:
    skip, limit = clamp_skip_limit(skip, limit)
    stmt = select(MfNavHistory).order_by(MfNavHistory.nav_date.desc(), MfNavHistory.scheme_code)
    if scheme_code:
        stmt = stmt.where(MfNavHistory.scheme_code == scheme_code)
    if isin:
        key = isin.strip().upper()
        stmt = stmt.where(
            MfNavHistory.isin.is_not(None),
            func.upper(MfNavHistory.isin) == key,
        )
    if nav_date_from:
        stmt = stmt.where(MfNavHistory.nav_date >= nav_date_from)
    if nav_date_to:
        stmt = stmt.where(MfNavHistory.nav_date <= nav_date_to)
    stmt = stmt.offset(skip).limit(limit)
    return list((await db.execute(stmt)).scalars().all())


async def get_nav_by_scheme(
    db: AsyncSession, scheme_code: str, *, nav_date: Optional[date] = None
) -> MfNavHistory:
    """One row: exact ``nav_date`` if given, else latest NAV for ``scheme_code``."""
    stmt = select(MfNavHistory).where(MfNavHistory.scheme_code == scheme_code)
    if nav_date is not None:
        stmt = stmt.where(MfNavHistory.nav_date == nav_date)
    else:
        stmt = stmt.order_by(MfNavHistory.nav_date.desc()).limit(1)
    row = (await db.execute(stmt)).scalar_one_or_none()
    if not row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="NAV row not found for this scheme_code (and nav_date if provided)",
        )
    return row


async def get_nav_on_scheme_date(db: AsyncSession, scheme_code: str, nav_date: date) -> MfNavHistory:
    """Exact row for (scheme_code, nav_date) — table natural key."""
    row = (
        await db.execute(
            select(MfNavHistory).where(
                MfNavHistory.scheme_code == scheme_code,
                MfNavHistory.nav_date == nav_date,
            )
        )
    ).scalar_one_or_none()
    if not row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No NAV row for this scheme_code on the given nav_date",
        )
    return row


async def get_nav_on_isin_date(db: AsyncSession, isin: str, nav_date: date) -> MfNavHistory:
    """Exact row for (isin, nav_date)."""
    isin_key = isin.strip().upper()
    row = (
        await db.execute(
            select(MfNavHistory).where(
                MfNavHistory.isin.is_not(None),
                func.upper(MfNavHistory.isin) == isin_key,
                MfNavHistory.nav_date == nav_date,
            )
        )
    ).scalar_one_or_none()
    if not row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No NAV row for this ISIN on the given nav_date",
        )
    return row


async def get_nav_by_isin(
    db: AsyncSession, isin: str, *, nav_date: Optional[date] = None
) -> MfNavHistory:
    """One row: exact ``nav_date`` if given, else latest NAV for this ISIN."""
    isin_key = isin.strip().upper()
    stmt = select(MfNavHistory).where(
        MfNavHistory.isin.is_not(None),
        func.upper(MfNavHistory.isin) == isin_key,
    )
    if nav_date is not None:
        stmt = stmt.where(MfNavHistory.nav_date == nav_date)
    else:
        stmt = stmt.order_by(MfNavHistory.nav_date.desc()).limit(1)
    row = (await db.execute(stmt)).scalar_one_or_none()
    if not row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="NAV row not found for this ISIN (and nav_date if provided)",
        )
    return row


async def create_nav_row(db: AsyncSession, payload: MfNavHistoryCreate) -> MfNavHistory:
    row = MfNavHistory(**payload.model_dump())
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


async def update_nav_on_scheme_date(
    db: AsyncSession, scheme_code: str, nav_date: date, payload: MfNavHistoryUpdate
) -> MfNavHistory:
    row = await get_nav_on_scheme_date(db, scheme_code, nav_date)
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(row, k, v)
    await db.commit()
    await db.refresh(row)
    return row


async def delete_nav_on_scheme_date(db: AsyncSession, scheme_code: str, nav_date: date) -> None:
    row = await get_nav_on_scheme_date(db, scheme_code, nav_date)
    await db.delete(row)
    await db.commit()


async def update_nav_on_isin_date(
    db: AsyncSession, isin: str, nav_date: date, payload: MfNavHistoryUpdate
) -> MfNavHistory:
    row = await get_nav_on_isin_date(db, isin, nav_date)
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(row, k, v)
    await db.commit()
    await db.refresh(row)
    return row


async def delete_nav_on_isin_date(db: AsyncSession, isin: str, nav_date: date) -> None:
    row = await get_nav_on_isin_date(db, isin, nav_date)
    await db.delete(row)
    await db.commit()


async def bulk_insert_nav_rows(db: AsyncSession, rows: Iterable[dict]) -> int:
    """Insert NAV rows in bulk with ``ON CONFLICT (scheme_code, nav_date) DO NOTHING``.

    Idempotent: re-runs of the same (scheme_code, nav_date) silently skip.
    Caller manages the transaction (no commit here).
    Returns the number of rows actually inserted.
    """
    payload = list(rows)
    if not payload:
        return 0
    stmt = pg_insert(MfNavHistory).values(payload)
    stmt = stmt.on_conflict_do_nothing(index_elements=["scheme_code", "nav_date"])
    result = await db.execute(stmt)
    return int(result.rowcount or 0)
