"""CRUD for ``mf_nav_history``."""

from __future__ import annotations

import logging
import uuid
from datetime import date
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional

import httpx
from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.mf import MfFundMetadata, MfNavHistory
from app.services.mf.mfapi_fetcher import MFAPI_TIMEOUT, fetch_scheme_detail
from app.schemas.mf import MfNavHistoryCreate, MfNavHistoryUpdate
from app.services.mf.paging import clamp_skip_limit

logger = logging.getLogger(__name__)
_LATEST_NAV_STALE_AFTER_DAYS = 1
_MIN_NAV_HISTORY_ROWS = 30


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
    if nav_date is None:
        latest = await get_latest_nav_with_source_fallback(db, scheme_code)
        if latest is not None:
            return latest
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="NAV row not found for this scheme_code (and nav_date if provided)",
        )

    stmt = select(MfNavHistory).where(MfNavHistory.scheme_code == scheme_code)
    stmt = stmt.where(MfNavHistory.nav_date == nav_date)
    row = (await db.execute(stmt)).scalar_one_or_none()
    if not row:
        fetched = await _fetch_nav_from_source_for_scheme(db, scheme_code, nav_date=nav_date)
        if fetched is not None:
            return fetched
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
    if nav_date is None:
        scheme_code = (
            await db.execute(
                select(MfFundMetadata.scheme_code).where(
                    MfFundMetadata.isin.is_not(None),
                    func.upper(MfFundMetadata.isin) == isin_key,
                )
            )
        ).scalar_one_or_none()
        if scheme_code:
            latest = await get_latest_nav_with_source_fallback(db, str(scheme_code))
            if latest is not None:
                return latest

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
        isin_key = isin.strip().upper()
        fetched = await _fetch_nav_from_source_for_isin(db, isin_key, nav_date=nav_date)
        if fetched is not None:
            return fetched
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


async def get_latest_nav_with_source_fallback(
    db: AsyncSession,
    scheme_code: str,
    *,
    stale_after_days: int = _LATEST_NAV_STALE_AFTER_DAYS,
) -> Optional[MfNavHistory]:
    """Return latest NAV; auto-refresh from source if missing/stale."""
    code = scheme_code.strip()
    if not code:
        return None

    existing = (
        await db.execute(
            select(MfNavHistory)
            .where(MfNavHistory.scheme_code == code)
            .order_by(MfNavHistory.nav_date.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    cutoff = (datetime.now(timezone.utc) - timedelta(days=stale_after_days)).date()
    row_count = int(
        (
            await db.execute(
                select(func.count()).select_from(MfNavHistory).where(MfNavHistory.scheme_code == code)
            )
        ).scalar()
        or 0
    )
    if existing is not None and existing.nav_date >= cutoff and row_count >= _MIN_NAV_HISTORY_ROWS:
        return existing

    try:
        fetched = await _fetch_nav_from_source_for_scheme(db, code, nav_date=None)
    except Exception:
        logger.exception("source NAV fallback failed for scheme_code=%s", code)
        fetched = None

    if fetched is None:
        return existing
    if existing is None or fetched.nav_date >= existing.nav_date:
        return fetched
    return existing


def _mf_type(detail_scheme_type: str, detail_scheme_category: str) -> str:
    value = " | ".join(p for p in (detail_scheme_type, detail_scheme_category) if p).strip()
    return value or "Unknown"


async def _fetch_nav_from_source_for_scheme(
    db: AsyncSession, scheme_code: str, *, nav_date: Optional[date]
) -> Optional[MfNavHistory]:
    code = scheme_code.strip()
    if not code:
        return None

    async with httpx.AsyncClient(timeout=MFAPI_TIMEOUT, follow_redirects=True) as client:
        detail = await fetch_scheme_detail(client, code)
    if detail is None or not detail.navs:
        return None

    if nav_date is not None:
        point = next((p for p in detail.navs if p.nav_date == nav_date), None)
    else:
        point = max(detail.navs, key=lambda p: p.nav_date)
    if point is None:
        return None

    # Return selected point immediately; persist full scheme NAV history.
    mf_type = _mf_type(detail.scheme_type, detail.scheme_category)
    full_nav_rows = [
        {
            "scheme_code": detail.scheme_code,
            "isin": detail.isin_growth,
            "scheme_name": detail.scheme_name,
            "mf_type": mf_type,
            "nav": nav_pt.nav,
            "nav_date": nav_pt.nav_date,
        }
        for nav_pt in detail.navs
    ]
    if not full_nav_rows:
        # Keep at least one row in rare cases where source has only older history.
        full_nav_rows = [
            {
                "scheme_code": detail.scheme_code,
                "isin": detail.isin_growth,
                "scheme_name": detail.scheme_name,
                "mf_type": mf_type,
                "nav": point.nav,
                "nav_date": point.nav_date,
            }
        ]
    nav_row = MfNavHistory(
        id=uuid.uuid4(),
        scheme_code=detail.scheme_code,
        isin=detail.isin_growth,
        scheme_name=detail.scheme_name,
        mf_type=mf_type,
        nav=point.nav,
        nav_date=point.nav_date,
        created_at=datetime.now(timezone.utc),
    )
    await _persist_source_nav_and_metadata(
        scheme_code=detail.scheme_code,
        isin=detail.isin_growth,
        isin_div_reinvest=detail.isin_div_reinvest,
        scheme_name=detail.scheme_name,
        amc_name=detail.fund_house or "Unknown",
        category=detail.scheme_category or "Unknown",
        plan_type=detail.plan_type,
        option_type=detail.option_type,
        nav_rows=full_nav_rows,
    )
    return nav_row


async def _fetch_nav_from_source_for_isin(
    db: AsyncSession, isin: str, *, nav_date: Optional[date]
) -> Optional[MfNavHistory]:
    scheme_code = (
        await db.execute(
            select(MfFundMetadata.scheme_code).where(
                MfFundMetadata.isin.is_not(None),
                func.upper(MfFundMetadata.isin) == isin,
            )
        )
    ).scalar_one_or_none()
    if not scheme_code:
        return None
    return await _fetch_nav_from_source_for_scheme(db, str(scheme_code), nav_date=nav_date)


async def _persist_source_nav_and_metadata(
    *,
    scheme_code: str,
    isin: Optional[str],
    isin_div_reinvest: Optional[str],
    scheme_name: str,
    amc_name: str,
    category: str,
    plan_type: object,
    option_type: object,
    nav_rows: list[dict],
) -> None:
    from app.database import _get_session_factory

    factory = _get_session_factory()
    async with factory() as bg_db:
        try:
            meta_stmt = pg_insert(MfFundMetadata).values(
                [
                    {
                        "scheme_code": scheme_code,
                        "isin": isin,
                        "isin_div_reinvest": isin_div_reinvest,
                        "scheme_name": scheme_name,
                        "amc_name": amc_name,
                        "category": category,
                        "sub_category": None,
                        "plan_type": plan_type,
                        "option_type": option_type,
                        "is_active": True,
                    }
                ]
            )
            meta_stmt = meta_stmt.on_conflict_do_update(
                index_elements=["scheme_code"],
                set_={
                    "isin": meta_stmt.excluded.isin,
                    "isin_div_reinvest": meta_stmt.excluded.isin_div_reinvest,
                    "scheme_name": meta_stmt.excluded.scheme_name,
                    "amc_name": meta_stmt.excluded.amc_name,
                    "category": meta_stmt.excluded.category,
                    "plan_type": meta_stmt.excluded.plan_type,
                    "option_type": meta_stmt.excluded.option_type,
                    "is_active": meta_stmt.excluded.is_active,
                },
            )
            await bg_db.execute(meta_stmt)
            await bulk_insert_nav_rows(bg_db, nav_rows)
            await bg_db.commit()
        except Exception:
            await bg_db.rollback()
