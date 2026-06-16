"""Backfill, incremental refresh, and read access for ``index_tri_history``.

Mirrors ``nav_history_service`` (pg upsert + chunking) and hosts the scheduled
job function the independent TRI scheduler calls. Caller manages the
transaction for write helpers (no commit here) except ``run_tri_refresh_job``,
which owns its own session + advisory lock.
"""

from __future__ import annotations

import logging
import time
from datetime import date, timedelta
from typing import Iterable, Optional

import httpx
from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.mutual_funds.models import IndexTriHistory
from app.domains.mutual_funds.services.niftyindices_fetcher import (
    DEFAULT_INDEX_NAME,
    NIFTY_TRI_EARLIEST,
    NIFTY_TRI_TIMEOUT,
    TriRow,
    fetch_tri_chunked,
)

logger = logging.getLogger(__name__)

INDEX_TRI_LOCK_KEY = 7421101  # distinct from MFAPI_LOCK_KEY (7421100)
_BULK_CHUNK_SIZE = 500


async def bulk_insert_tri_rows(
    db: AsyncSession, index_name: str, rows: Iterable[TriRow]
) -> int:
    """Insert rows with ON CONFLICT (index_name, tri_date) DO NOTHING. Idempotent."""
    payload = [
        {
            "index_name": index_name,
            "tri_date": r.tri_date,
            "tri_value": r.tri_value,
            "ntr_value": r.ntr_value,
        }
        for r in rows
    ]
    if not payload:
        return 0
    total = 0
    for start in range(0, len(payload), _BULK_CHUNK_SIZE):
        chunk = payload[start : start + _BULK_CHUNK_SIZE]
        stmt = pg_insert(IndexTriHistory).values(chunk)
        stmt = stmt.on_conflict_do_nothing(index_elements=["index_name", "tri_date"])
        result = await db.execute(stmt)
        total += int(result.rowcount or 0)
    return total


async def _max_tri_date(db: AsyncSession, index_name: str) -> Optional[date]:
    return (
        await db.execute(
            select(func.max(IndexTriHistory.tri_date)).where(
                IndexTriHistory.index_name == index_name
            )
        )
    ).scalar()


def _incremental_start(high_water_mark: Optional[date]) -> date:
    """First date to fetch: day after the latest stored row, or earliest if empty."""
    if high_water_mark is None:
        return NIFTY_TRI_EARLIEST
    return high_water_mark + timedelta(days=1)


async def backfill_full_history(
    db: AsyncSession, index_name: str = DEFAULT_INDEX_NAME
) -> int:
    """Fetch full available history (earliest -> today) and bulk insert. Re-run safe."""
    today = date.today()
    async with httpx.AsyncClient(timeout=NIFTY_TRI_TIMEOUT) as client:
        rows = await fetch_tri_chunked(client, index_name, NIFTY_TRI_EARLIEST, today)
    inserted = await bulk_insert_tri_rows(db, index_name, rows)
    logger.info(
        "TRI backfill %s: fetched=%d inserted=%d", index_name, len(rows), inserted
    )
    return inserted


async def refresh_incremental(
    db: AsyncSession, index_name: str = DEFAULT_INDEX_NAME
) -> int:
    """Fetch only rows newer than the stored high-water mark and bulk insert."""
    start = _incremental_start(await _max_tri_date(db, index_name))
    today = date.today()
    if start > today:
        logger.info("TRI refresh %s: up to date (start %s > today)", index_name, start)
        return 0
    async with httpx.AsyncClient(timeout=NIFTY_TRI_TIMEOUT) as client:
        rows = await fetch_tri_chunked(client, index_name, start, today)
    inserted = await bulk_insert_tri_rows(db, index_name, rows)
    logger.info(
        "TRI refresh %s: from=%s fetched=%d inserted=%d",
        index_name,
        start,
        len(rows),
        inserted,
    )
    return inserted


async def get_tri_on_or_before(
    db: AsyncSession, index_name: str, on: date
) -> Optional[IndexTriHistory]:
    """Nearest trading-day TRI row with tri_date <= ``on`` (for benchmark valuation)."""
    return (
        await db.execute(
            select(IndexTriHistory)
            .where(
                IndexTriHistory.index_name == index_name, IndexTriHistory.tri_date <= on
            )
            .order_by(IndexTriHistory.tri_date.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def run_tri_refresh_job(index_name: str = DEFAULT_INDEX_NAME) -> None:
    """Scheduled entry: own session + advisory lock, then incremental refresh.

    First run against an empty table naturally backfills full history (the
    high-water mark is None -> start = earliest date).
    """
    from app.core.database import _get_session_factory

    t0 = time.monotonic()
    factory = _get_session_factory()
    try:
        async with factory() as db:
            got_lock = (
                await db.execute(
                    text("SELECT pg_try_advisory_lock(:k)"), {"k": INDEX_TRI_LOCK_KEY}
                )
            ).scalar()
            if not got_lock:
                logger.info("TRI job: lock held by another worker; skipping")
                return
            try:
                inserted = await refresh_incremental(db, index_name)
                await db.commit()
                logger.info(
                    "TRI job done in %.1fs: inserted=%d",
                    time.monotonic() - t0,
                    inserted,
                )
            except Exception:
                await db.rollback()
                logger.exception("TRI job crashed after %.1fs", time.monotonic() - t0)
            finally:
                try:
                    await db.execute(
                        text("SELECT pg_advisory_unlock(:k)"), {"k": INDEX_TRI_LOCK_KEY}
                    )
                except SQLAlchemyError:
                    logger.warning(
                        "TRI job: failed to release advisory lock", exc_info=True
                    )
    except SQLAlchemyError:
        logger.warning(
            "TRI job: database unavailable; will retry next schedule", exc_info=True
        )
