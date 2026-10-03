"""Phase A: make sure NAV history reaches back to when each fund was actually held.

The daily mfapi.in top-up only ever appends *today's* NAV, so a fund the user bought in
2014 can easily have three weeks of stored history and nothing before it. Valuing it
over the missing decade is not a rounding error — those units price at zero for every
day before the stored window while their full cost still counts against them, and the
chart opens on a total loss the user never took.

The old version asked that question one fund at a time: ``ensure_nav_history_for_chart``
runs two or three ``COUNT``/``MIN``/``MAX`` queries per scheme before it can decide
whether a fetch is even needed, so a 60-fund portfolio spent ~180 round trips deciding
that most funds were already fine. Here **one** grouped query answers it for the whole
portfolio, and only the genuine gaps go on to fetch.
"""

from __future__ import annotations

import asyncio
import logging
from collections import Counter
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import String, bindparam, text
from sqlalchemy.dialects.postgresql import ARRAY

from app.core.database import _get_session_factory
from app.core.job_tracing import suppress_instrumentation
from app.domains.mutual_funds.services.nav_history_service import (
    ensure_nav_history_for_chart,
)

logger = logging.getLogger(__name__)

# mfapi.in is a public feed that 502s under bursts, and the fetcher already retries with
# backoff. Six in flight is roughly a 6x wall-clock win on a large portfolio while still
# being a good citizen. Each task takes its OWN session — an AsyncSession is not safe to
# share across concurrent tasks.
NAV_FETCH_CONCURRENCY = 6

# Trailing staleness we will not bother fetching for: the daily NAV job runs three times
# a day, so anything inside a long weekend is simply "not published yet".
TRAILING_STALE_DAYS = 7


@dataclass
class CoverageGap:
    nav_key: str
    need_from: date
    have_rows: int
    have_from: date | None
    have_to: date | None


async def find_gaps(
    db, nav_key_first_txn: dict[str, date], today: date
) -> list[CoverageGap]:
    """One query: which of these funds lack NAV history over the window they were held?

    A fund needs fetching when it has no stored NAV at all, when its stored history
    starts materially after the fund was first held (the leading gap that
    ``require_from_start`` exists for), or when its newest NAV has fallen behind.
    """
    if not nav_key_first_txn:
        return []

    keys = sorted(nav_key_first_txn)
    rows = (
        await db.execute(
            text(
                "SELECT scheme_code, COUNT(*) AS have_rows, "
                "       MIN(nav_date) AS have_from, MAX(nav_date) AS have_to "
                "FROM mf_nav_history "
                "WHERE scheme_code = ANY(:codes) AND nav > 0 "
                "GROUP BY scheme_code"
            ).bindparams(bindparam("codes", type_=ARRAY(String))),
            {"codes": keys},
        )
    ).all()
    stored = {row.scheme_code: row for row in rows}

    trailing_cutoff = today - timedelta(days=TRAILING_STALE_DAYS)
    gaps: list[CoverageGap] = []
    for key in keys:
        need_from = nav_key_first_txn[key]
        row = stored.get(key)
        if row is None or not row.have_rows:
            gaps.append(CoverageGap(key, need_from, 0, None, None))
            continue
        # ``ensure_nav_history_for_chart`` applies its own 30-day leading-gap
        # tolerance; anything inside that it will decline to fetch anyway, so asking
        # is free but pointless. Send it the clear cases.
        leading_gap = row.have_from is not None and row.have_from > need_from
        trailing_gap = row.have_to is not None and row.have_to < trailing_cutoff
        if leading_gap or trailing_gap:
            gaps.append(
                CoverageGap(
                    key, need_from, int(row.have_rows), row.have_from, row.have_to
                )
            )
    return gaps


async def _fetch_one(gap: CoverageGap, today: date, counts: Counter) -> None:
    """Backfill one fund's NAV on a private session. Never raises.

    A fund we could not fetch still gets valued — off its stated NAV or its last
    published one — so one dead upstream must not fail the build. It does get counted,
    because a series priced off stale data should say so rather than look authoritative.
    """
    factory = _get_session_factory()
    try:
        async with factory() as db:
            await ensure_nav_history_for_chart(
                db,
                gap.nav_key,
                date_from=gap.need_from,
                date_to=today,
                # The fund was demonstrably HELD from this date, so stored history
                # that starts later is a gap to close, not a young fund.
                require_from_start=True,
            )
        counts["nav_fetched"] += 1
    except Exception:  # noqa: BLE001 — best effort, per scheme
        counts["nav_fetch_failed"] += 1
        logger.exception("networth: NAV fetch failed for scheme %s", gap.nav_key)


async def close_gaps(
    gaps: list[CoverageGap],
    today: date,
    *,
    on_progress=None,
) -> Counter:
    """Fetch every gap with bounded concurrency. Returns per-outcome counts."""
    counts: Counter = Counter()
    if not gaps:
        return counts

    total = len(gaps)
    done = 0
    sem = asyncio.Semaphore(NAV_FETCH_CONCURRENCY)
    lock = asyncio.Lock()

    async def _one(gap: CoverageGap) -> None:
        nonlocal done
        async with sem:
            await _fetch_one(gap, today, counts)
        async with lock:
            done += 1
            if on_progress is not None:
                await on_progress(done, total)

    # SQLAlchemy and httpx are instrumented globally, so an un-suppressed bulk loop
    # emits tens of thousands of spans into a single trace that nothing can render.
    with suppress_instrumentation():
        await asyncio.gather(*(_one(gap) for gap in gaps))

    return counts
