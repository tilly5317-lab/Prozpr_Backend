"""Backfill, EOD refresh, and read access for benchmark index values.

Owns the catalogue (``benchmark_index``) + daily EOD values
(``benchmark_index_value``). Write helpers leave the transaction to the caller
(no commit) except ``run_benchmark_refresh_job``, which owns its own session +
Postgres advisory lock.

Refresh model (per product spec): the scheduler runs thrice a day; each run
re-fetches a short trailing window and **upserts (DO UPDATE)** so the just-
published EOD value — and any later NSE revision — overwrites that date's row.
The value for date X is X's EOD close; reads use "nearest value_date <= on", so
"today" resolves to yesterday's EOD until tonight's close publishes.
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

from app.domains.benchmarks.models import BenchmarkIndex, BenchmarkIndexValue
from app.domains.benchmarks.services.niftyindices_fetcher import (
    NIFTY_TRI_EARLIEST,
    NIFTY_TRI_TIMEOUT,
    TriRow,
    fetch_tri_chunked,
)

logger = logging.getLogger(__name__)

# Reuse the historical advisory-lock key so only one worker refreshes at a time.
BENCHMARK_LOCK_KEY = 7421101
_BULK_CHUNK_SIZE = 500

# How far back a brand-new (empty) index is seeded on first refresh.
BACKFILL_YEARS = 5
# Trailing window the thrice-daily job re-fetches to catch the new EOD + revisions.
DEFAULT_LOOKBACK_DAYS = 10

NIFTY_50_CODE = "NIFTY 50"

# Catalogue metadata used when an index is auto-created. Extend for new indices.
KNOWN_INDICES: dict[str, dict] = {
    NIFTY_50_CODE: {
        "display_name": "Nifty 50",
        "short_name": "Nifty 50",
        "provider": "NSE",
        "asset_class": "EQUITY",
        "description": "NSE Nifty 50 Total Return Index (gross + net).",
        "earliest_available": NIFTY_TRI_EARLIEST,
    },
}


def _years_ago(years: int, today: Optional[date] = None) -> date:
    today = today or date.today()
    try:
        return today.replace(year=today.year - years)
    except ValueError:  # Feb 29 -> Feb 28 in a non-leap target year
        return today.replace(year=today.year - years, day=28)


# ---------------------------------------------------------------------------
# Catalogue
# ---------------------------------------------------------------------------


async def get_index_by_code(db: AsyncSession, code: str) -> Optional[BenchmarkIndex]:
    return (
        await db.execute(select(BenchmarkIndex).where(BenchmarkIndex.code == code))
    ).scalar_one_or_none()


async def get_or_create_index(db: AsyncSession, code: str) -> BenchmarkIndex:
    """Return the catalogue row for ``code``, creating it from KNOWN_INDICES if absent.

    Does not commit — the caller owns the transaction.
    """
    existing = await get_index_by_code(db, code)
    if existing is not None:
        return existing
    meta = KNOWN_INDICES.get(
        code,
        {"display_name": code, "short_name": code, "provider": "NSE",
         "asset_class": "EQUITY", "description": None, "earliest_available": None},
    )
    index = BenchmarkIndex(code=code, **meta)
    db.add(index)
    await db.flush()  # populate index.id for FK use within the same txn
    return index


async def list_indices(
    db: AsyncSession, *, active_only: bool = False
) -> list[BenchmarkIndex]:
    stmt = select(BenchmarkIndex).order_by(BenchmarkIndex.display_name.asc())
    if active_only:
        stmt = stmt.where(BenchmarkIndex.is_active.is_(True))
    return list((await db.execute(stmt)).scalars().all())


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------


async def bulk_upsert_values(
    db: AsyncSession, index_id, rows: Iterable[TriRow]
) -> int:
    """Upsert rows with ON CONFLICT (benchmark_index_id, value_date) DO UPDATE.

    DO UPDATE (not DO NOTHING) so a later run overwrites a date's value when the
    EOD newly publishes or NSE revises it. Returns affected row count.
    """
    payload = [
        {
            "benchmark_index_id": index_id,
            "value_date": r.tri_date,
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
        stmt = pg_insert(BenchmarkIndexValue).values(chunk)
        stmt = stmt.on_conflict_do_update(
            index_elements=["benchmark_index_id", "value_date"],
            set_={
                "tri_value": stmt.excluded.tri_value,
                "ntr_value": stmt.excluded.ntr_value,
            },
        )
        result = await db.execute(stmt)
        total += int(result.rowcount or 0)
    return total


async def _fetch_rows(code: str, start: date, end: date) -> list[TriRow]:
    async with httpx.AsyncClient(timeout=NIFTY_TRI_TIMEOUT) as client:
        return await fetch_tri_chunked(client, code, start, end)


async def backfill_range(
    db: AsyncSession, code: str, start: date, end: date
) -> int:
    """Fetch [start, end] for ``code`` and upsert. Re-run safe. No commit."""
    index = await get_or_create_index(db, code)
    rows = await _fetch_rows(code, start, end)
    upserted = await bulk_upsert_values(db, index.id, rows)
    logger.info(
        "Benchmark backfill %s %s..%s: fetched=%d upserted=%d",
        code,
        start,
        end,
        len(rows),
        upserted,
    )
    return upserted


async def refresh_recent(
    db: AsyncSession, code: str, *, lookback_days: int = DEFAULT_LOOKBACK_DAYS
) -> int:
    """Re-fetch the trailing ``lookback_days`` window and upsert. No commit.

    If the index has no rows yet, seed it with a ``BACKFILL_YEARS`` backfill
    instead (the thrice-daily job's first run effectively bootstraps the index).
    """
    index = await get_or_create_index(db, code)
    today = date.today()
    has_rows = (
        await db.execute(
            select(func.count())
            .select_from(BenchmarkIndexValue)
            .where(BenchmarkIndexValue.benchmark_index_id == index.id)
        )
    ).scalar_one()
    if not has_rows:
        return await backfill_range(db, code, _years_ago(BACKFILL_YEARS, today), today)
    start = today - timedelta(days=lookback_days)
    rows = await _fetch_rows(code, start, today)
    upserted = await bulk_upsert_values(db, index.id, rows)
    logger.info(
        "Benchmark refresh %s from=%s fetched=%d upserted=%d",
        code,
        start,
        len(rows),
        upserted,
    )
    return upserted


# ---------------------------------------------------------------------------
# Reads (used by portfolio analytics + the read API)
# ---------------------------------------------------------------------------


async def load_value_series(
    db: AsyncSession, code: str
) -> list[tuple[date, float]]:
    """All (value_date, tri_value) for ``code``, ascending — for benchmark lookups."""
    rows = (
        await db.execute(
            select(BenchmarkIndexValue.value_date, BenchmarkIndexValue.tri_value)
            .join(
                BenchmarkIndex,
                BenchmarkIndexValue.benchmark_index_id == BenchmarkIndex.id,
            )
            .where(BenchmarkIndex.code == code)
            .order_by(BenchmarkIndexValue.value_date.asc())
        )
    ).all()
    return [(d, float(v)) for d, v in rows]


async def get_value_on_or_before(
    db: AsyncSession, code: str, on: date
) -> Optional[BenchmarkIndexValue]:
    """Nearest EOD value with value_date <= ``on`` (gives yesterday's close for today)."""
    return (
        await db.execute(
            select(BenchmarkIndexValue)
            .join(
                BenchmarkIndex,
                BenchmarkIndexValue.benchmark_index_id == BenchmarkIndex.id,
            )
            .where(BenchmarkIndex.code == code, BenchmarkIndexValue.value_date <= on)
            .order_by(BenchmarkIndexValue.value_date.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def get_latest_value(
    db: AsyncSession, code: str
) -> Optional[BenchmarkIndexValue]:
    return await get_value_on_or_before(db, code, date.today())


# ---------------------------------------------------------------------------
# Scheduled job entry
# ---------------------------------------------------------------------------


async def run_benchmark_refresh_job() -> None:
    """Scheduled entry (fired 3x/day): own session + advisory lock, refresh actives.

    Ensures the default Nifty 50 catalogue row exists, then refreshes every
    active index's trailing EOD window (empty indices bootstrap via backfill).
    Serialized across uvicorn workers by a Postgres advisory lock.
    """
    from app.core.database import _get_session_factory

    t0 = time.monotonic()
    factory = _get_session_factory()
    try:
        async with factory() as db:
            got_lock = (
                await db.execute(
                    text("SELECT pg_try_advisory_lock(:k)"), {"k": BENCHMARK_LOCK_KEY}
                )
            ).scalar()
            if not got_lock:
                logger.info("Benchmark job: lock held by another worker; skipping")
                return
            try:
                await get_or_create_index(db, NIFTY_50_CODE)
                indices = await list_indices(db, active_only=True)
                total = 0
                for index in indices:
                    total += await refresh_recent(db, index.code)
                await db.commit()
                logger.info(
                    "Benchmark job done in %.1fs: indices=%d upserted=%d",
                    time.monotonic() - t0,
                    len(indices),
                    total,
                )
            except Exception:
                await db.rollback()
                logger.exception(
                    "Benchmark job crashed after %.1fs", time.monotonic() - t0
                )
            finally:
                try:
                    await db.execute(
                        text("SELECT pg_advisory_unlock(:k)"),
                        {"k": BENCHMARK_LOCK_KEY},
                    )
                except SQLAlchemyError:
                    logger.warning(
                        "Benchmark job: failed to release advisory lock", exc_info=True
                    )
    except SQLAlchemyError:
        logger.warning(
            "Benchmark job: database unavailable; will retry next schedule",
            exc_info=True,
        )
