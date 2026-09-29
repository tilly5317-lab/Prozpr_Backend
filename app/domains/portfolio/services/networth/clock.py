"""The one clock the net-worth subsystem is allowed to read.

Every "today" in this package means **today in India**, because that is the day the
user sees on their statement and the day an AMC stamps on a NAV. The process runs on a
UTC box, so ``date.today()`` is the *server's* calendar day — for roughly five and a
half hours out of every twenty-four the two disagree, and the NAV/net-worth crons sit
close enough to that boundary that the bug is one schedule change away from being real.

IST is a fixed UTC+5:30 with no DST, so this needs no tz database — the same derivation
``mfapi_scheduler._min_nav_date_for_daily_refresh`` already uses.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

IST_OFFSET = timedelta(hours=5, minutes=30)


def ist_now() -> datetime:
    """Current wall-clock time in India, as a naive datetime."""
    return datetime.now(timezone.utc).replace(tzinfo=None) + IST_OFFSET


def ist_today() -> date:
    """Today's calendar date in India."""
    return ist_now().date()


def utc_now() -> datetime:
    """Timezone-aware UTC, for stamping ``started_at`` / ``finished_at`` columns."""
    return datetime.now(timezone.utc)
