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


_BULK_CHUNK_SIZE = 500


async def bulk_insert_nav_rows(db: AsyncSession, rows: Iterable[dict]) -> int:
    """Insert NAV rows in bulk with ``ON CONFLICT (scheme_code, nav_date) DO NOTHING``.

    Idempotent: re-runs of the same (scheme_code, nav_date) silently skip.
    Caller manages the transaction (no commit here).
    Returns the number of rows actually inserted.

    Large payloads are chunked to stay well within PostgreSQL's bind-parameter
    limit (~65 535) and to keep memory pressure reasonable.
    """
    payload = list(rows)
    if not payload:
        return 0
    total = 0
    for start in range(0, len(payload), _BULK_CHUNK_SIZE):
        chunk = payload[start : start + _BULK_CHUNK_SIZE]
        stmt = pg_insert(MfNavHistory).values(chunk)
        stmt = stmt.on_conflict_do_nothing(index_elements=["scheme_code", "nav_date"])
        result = await db.execute(stmt)
        total += int(result.rowcount or 0)
    return total


async def get_latest_nav_with_source_fallback(
    db: AsyncSession,
    scheme_code: str,
    *,
    stale_after_days: int = _LATEST_NAV_STALE_AFTER_DAYS,
) -> Optional[MfNavHistory]:
    """Return latest NAV; auto-refresh from source if missing/stale.

    Fetches from mfapi.in only when:
    - No local NAV exists at all, OR
    - The latest local NAV is older than ``stale_after_days``.

    Chart history backfill for new funds is handled by the nightly scheduler;
    this path only tops up the latest NAV when stale.
    """
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
    if existing is not None and existing.nav_date >= cutoff:
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


def _min_rows_for_chart_range(date_from: date, date_to: date) -> int:
    """Rough minimum NAV points expected for a chart over ``date_from``..``date_to``."""
    span_days = max((date_to - date_from).days, 1)
    return max(30, min(500, span_days // 3))


async def ensure_nav_history_for_chart(
    db: AsyncSession,
    scheme_code: str,
    *,
    date_from: date,
    date_to: date,
) -> None:
    """Backfill ``mf_nav_history`` from mfapi.in when the DB is too sparse for charts.

    Only calls mfapi.in when stored history is clearly incomplete. Young funds that
    launched after ``date_from`` are not expected to have 10 years of rows — the
    target row count is based on ``max(date_from, fund_inception)``..``date_to``.
    """
    code = scheme_code.strip()
    if not code:
        return

    g_count, g_earliest, g_latest = (
        await db.execute(
            select(
                func.count(MfNavHistory.id),
                func.min(MfNavHistory.nav_date),
                func.max(MfNavHistory.nav_date),
            ).where(MfNavHistory.scheme_code == code)
        )
    ).one()
    stored = int(g_count or 0)

    if stored == 0:
        logger.info("scheme %s chart backfill: no NAV rows in DB yet", code)
        await _backfill_scheme_nav_history(
            db, code, date_from=date_from, date_to=date_to,
        )
        return

    coverage_start = max(date_from, g_earliest) if g_earliest else date_from
    min_rows = _min_rows_for_chart_range(coverage_start, date_to)

    in_window = int(
        (
            await db.execute(
                select(func.count(MfNavHistory.id)).where(
                    MfNavHistory.scheme_code == code,
                    MfNavHistory.nav_date >= date_from,
                    MfNavHistory.nav_date <= date_to,
                )
            )
        ).scalar()
        or 0
    )

    if in_window >= min_rows:
        logger.debug(
            "scheme %s chart OK: %d rows in window (need %d from %s; inception %s)",
            code,
            in_window,
            min_rows,
            coverage_start,
            g_earliest,
        )
        return

    logger.info(
        "scheme %s chart backfill: %d rows in [%s..%s] (inception=%s); need >= %d from %s",
        code,
        in_window,
        date_from,
        date_to,
        g_earliest,
        min_rows,
        coverage_start,
    )
    inserted = await _backfill_scheme_nav_history(
        db, code, date_from=date_from, date_to=date_to,
    )
    if inserted == 0 and stored > 0:
        logger.debug(
            "scheme %s chart: mfapi had nothing new (%d rows already stored)",
            code,
            stored,
        )


async def _backfill_scheme_nav_history(
    db: AsyncSession,
    scheme_code: str,
    *,
    date_from: date,
    date_to: date,
) -> int:
    """Persist all mfapi.in NAV points in ``date_from``..``date_to`` (ON CONFLICT skip)."""
    code = scheme_code.strip()
    if not code:
        return 0

    async with httpx.AsyncClient(timeout=MFAPI_TIMEOUT, follow_redirects=True) as client:
        detail = await fetch_scheme_detail(client, code)
    if detail is None or not detail.navs:
        return 0

    mf_type = _mf_type(detail.scheme_type, detail.scheme_category)
    in_range = [p for p in detail.navs if date_from <= p.nav_date <= date_to]
    if not in_range:
        in_range = list(detail.navs)

    nav_rows = [
        {
            "scheme_code": detail.scheme_code,
            "isin": detail.isin_growth,
            "scheme_name": detail.scheme_name,
            "mf_type": mf_type,
            "nav": nav_pt.nav,
            "nav_date": nav_pt.nav_date,
        }
        for nav_pt in in_range
    ]
    if not nav_rows:
        return 0

    inserted = await _persist_source_nav_and_metadata(
        scheme_code=detail.scheme_code,
        isin=detail.isin_growth,
        isin_div_reinvest=detail.isin_div_reinvest,
        scheme_name=detail.scheme_name,
        amc_name=detail.fund_house or "Unknown",
        category=detail.scheme_category or "Unknown",
        plan_type=detail.plan_type,
        option_type=detail.option_type,
        nav_rows=nav_rows,
    )
    logger.info(
        "scheme %s chart backfill done: persisted %d rows (mfapi had %d in range)",
        code,
        inserted,
        len(nav_rows),
    )
    return inserted


async def _fetch_nav_from_source_for_scheme(
    db: AsyncSession, scheme_code: str, *, nav_date: Optional[date]
) -> Optional[MfNavHistory]:
    code = scheme_code.strip()
    if not code:
        return None

    high_water = (
        await db.execute(
            select(func.max(MfNavHistory.nav_date)).where(
                MfNavHistory.scheme_code == code
            )
        )
    ).scalar()

    freshness_cutoff = (
        datetime.now(timezone.utc) - timedelta(days=_LATEST_NAV_STALE_AFTER_DAYS)
    ).date()
    if nav_date is None and high_water is not None and high_water >= freshness_cutoff:
        existing = (
            await db.execute(
                select(MfNavHistory)
                .where(MfNavHistory.scheme_code == code)
                .order_by(MfNavHistory.nav_date.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if existing is not None:
            logger.debug(
                "scheme %s NAV already current (latest=%s); skipping mfapi.in",
                code,
                high_water,
            )
            return existing

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

    mf_type = _mf_type(detail.scheme_type, detail.scheme_category)

    if high_water is not None:
        new_navs = [p for p in detail.navs if p.nav_date > high_water]
    else:
        new_navs = list(detail.navs)

    nav_rows_to_persist = [
        {
            "scheme_code": detail.scheme_code,
            "isin": detail.isin_growth,
            "scheme_name": detail.scheme_name,
            "mf_type": mf_type,
            "nav": nav_pt.nav,
            "nav_date": nav_pt.nav_date,
        }
        for nav_pt in new_navs
    ]

    if nav_rows_to_persist:
        logger.info(
            "scheme %s: mfapi returned %d points; persisting %d new (high-water=%s)",
            code,
            len(detail.navs),
            len(nav_rows_to_persist),
            high_water or "none",
        )
    else:
        logger.debug(
            "scheme %s: mfapi returned %d points; nothing new after high-water %s",
            code,
            len(detail.navs),
            high_water,
        )

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

    if nav_rows_to_persist:
        await _persist_source_nav_and_metadata(
            scheme_code=detail.scheme_code,
            isin=detail.isin_growth,
            isin_div_reinvest=detail.isin_div_reinvest,
            scheme_name=detail.scheme_name,
            amc_name=detail.fund_house or "Unknown",
            category=detail.scheme_category or "Unknown",
            plan_type=detail.plan_type,
            option_type=detail.option_type,
            nav_rows=nav_rows_to_persist,
        )
    else:
        logger.debug("No new NAV rows to persist for scheme %s (all up-to-date)", code)

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
) -> int:
    """Persist fund metadata and NAV rows in a background session.

    Returns the number of NAV rows actually inserted (0 on failure).
    """
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
            inserted = await bulk_insert_nav_rows(bg_db, nav_rows)
            await bg_db.commit()
            logger.info(
                "Persisted %d NAV rows for scheme %s (offered %d)",
                inserted, scheme_code, len(nav_rows),
            )
            return inserted
        except Exception:
            logger.exception(
                "Failed to persist NAV rows for scheme %s (%d rows offered)",
                scheme_code, len(nav_rows),
            )
            await bg_db.rollback()
            return 0
