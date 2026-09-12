"""The read path: horizon slices, server-side downsampling, and series metadata.

Two deliberate departures from the version this replaces.

**Reads do not write.** The old ``GET /portfolio/nav-history`` called a "top up the
trailing edge" helper on every dashboard load — a write on the hottest read path, on a
page every user opens first. Freshness is the daily job's job (``daily.py``); this
module reports staleness instead of fixing it, and one stale point is a far smaller
problem than write amplification and lock contention across the fleet.

**Long horizons are downsampled in SQL.** ``MAX`` on a twenty-year ledger is ~7,300
points: roughly a megabyte of JSON for a chart a few hundred pixels wide, every point of
which the browser then has to lay out. Bucketing to ~400 keeps the shape and drops the
weight. Every returned point is a real stored day — the last day of its bucket, the same
convention a candlestick close uses — so nothing on the chart is interpolated or
invented.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.portfolio.models.user_networth_series_state import (
    UserNetworthSeriesState,
)
from app.domains.portfolio.services.networth.clock import ist_today

HORIZON_DAYS: dict[str, int] = {
    "1M": 30,
    "3M": 90,
    "1Y": 365,
    "3Y": 365 * 3,
    # MAX = true since-inception: no cutoff. The series itself runs from the first
    # transaction to today, so this spans the user's entire history rather than a
    # fixed window.
    "MAX": 0,
}

# Points we aim to return for any horizon. Above roughly this many, a line chart is
# drawing several samples per pixel.
TARGET_POINTS = 400

# A series whose newest point is older than this is stale enough to say so.
STALE_AFTER_DAYS = 2


@dataclass
class NavPoint:
    recorded_date: date
    total_value: Decimal
    total_invested: Decimal
    gain_percentage: Decimal


_BUCKETED = text(
    """
    WITH win AS (
        SELECT recorded_date, total_value, total_invested, gain_percentage
        FROM   user_portfolio_nav_history
        WHERE  user_id = :user_id
          AND  (CAST(:cutoff AS date) IS NULL
                OR recorded_date >= CAST(:cutoff AS date))
    ),
    bucketed AS (
        SELECT *, ntile(:buckets) OVER (ORDER BY recorded_date) AS bucket
        FROM   win
    ),
    -- The last day of each bucket: a real stored point, never an average.
    picked AS (
        SELECT DISTINCT ON (bucket)
               recorded_date, total_value, total_invested, gain_percentage
        FROM   bucketed
        ORDER  BY bucket, recorded_date DESC
    ),
    -- ntile's first bucket contributes its LAST day, so without this the chart would
    -- silently start weeks after the user's first transaction.
    opening AS (
        SELECT recorded_date, total_value, total_invested, gain_percentage
        FROM   win ORDER BY recorded_date ASC LIMIT 1
    )
    SELECT recorded_date, total_value, total_invested, gain_percentage FROM picked
    UNION
    SELECT recorded_date, total_value, total_invested, gain_percentage FROM opening
    """
)


async def get_user_nav_history(
    db: AsyncSession, user_id: uuid.UUID, *, horizon: str
) -> list[NavPoint]:
    """Stored series for the chosen horizon, downsampled when it is long.

    Read-only, and no synthetic fallback: when the series is empty the dashboard shows
    an honest empty state and the build call-to-action, never a fabricated line.
    """
    horizon_u = (horizon or "1Y").upper()
    days = HORIZON_DAYS.get(horizon_u, HORIZON_DAYS["1Y"])
    cutoff: Optional[date] = ist_today() - timedelta(days=days) if days > 0 else None

    rows = (
        await db.execute(
            _BUCKETED,
            {"user_id": user_id, "cutoff": cutoff, "buckets": TARGET_POINTS},
        )
    ).all()

    points = [
        NavPoint(
            recorded_date=row.recorded_date,
            total_value=row.total_value,
            total_invested=row.total_invested,
            gain_percentage=row.gain_percentage,
        )
        for row in rows
    ]
    # UNION does not preserve order, and the appended first point lands arbitrarily.
    points.sort(key=lambda p: p.recorded_date)
    return points


async def get_series_state(
    db: AsyncSession, user_id: uuid.UUID
) -> Optional[UserNetworthSeriesState]:
    """The user's series summary — one primary-key lookup.

    This is what replaced a ``COUNT(*)`` for "does this user have history?" and a
    ``MAX(recorded_date)`` for "how fresh is it?", both of which the status endpoint
    used to run on a per-user 1.8-second poll.
    """
    return (
        await db.execute(
            select(UserNetworthSeriesState).where(
                UserNetworthSeriesState.user_id == user_id
            )
        )
    ).scalar_one_or_none()


def is_stale(state: Optional[UserNetworthSeriesState]) -> bool:
    """True when the newest point is old enough that the UI should qualify it."""
    if state is None or state.last_date is None:
        return False
    return (ist_today() - state.last_date).days > STALE_AFTER_DAYS
