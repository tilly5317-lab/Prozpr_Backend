"""Real per-user daily net-worth history.

Unlike the legacy synthetic builder (which interpolated each holding's
``return_1y/3y/5y`` with noise), this computes the **actual** daily portfolio
value from two honest sources:

* ``mf_transaction`` — the CAMS/KFintech ledger (real units, NAV, dates).
* ``mf_nav_history`` — the daily NAV per scheme fetched from mfapi.in.

For every day from the user's first transaction to today we hold, per scheme,
the running unit balance and multiply by that scheme's NAV on the day (carrying
the last published NAV forward across non-trading days). Summed across schemes
and stored one row per day in ``user_portfolio_nav_history``.

The work runs as a background job (``run_networth_backfill``) that first ensures
NAV history is fetched back to each fund's first transaction date (Phase A) and
then computes the series (Phase B), updating ``portfolio_networth_jobs`` with a
real % so the UI can show progress.
"""

from __future__ import annotations

import logging
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Awaitable, Callable, Optional

from sqlalchemy import delete, func, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import _get_session_factory
from app.core.job_tracing import record_job_counts, traced_job
from app.domains.mutual_funds.models import MfNavHistory, MfTransaction
from app.domains.mutual_funds.models.enums import MfTransactionType
from app.domains.mutual_funds.services.nav_history_service import (
    ensure_nav_history_for_chart,
)
from app.domains.portfolio.models.portfolio_networth_job import PortfolioNetworthJob
from app.domains.portfolio.models.user_portfolio_nav_history import (
    UserPortfolioNavHistory,
)
from app.domains.portfolio.services.portfolio_service import (
    revalue_primary_portfolio_at_latest_nav,
)

logger = logging.getLogger(__name__)

# Transactions that add units to the holding (cash out / units in).
_UNITS_IN_TYPES = {
    MfTransactionType.BUY,
    MfTransactionType.SWITCH_IN,
    MfTransactionType.DIVIDEND_REINVEST,
}
# Transactions that remove units (cash in / units out).
_UNITS_OUT_TYPES = {
    MfTransactionType.SELL,
    MfTransactionType.SWITCH_OUT,
}

ProgressCb = Callable[[float], Awaitable[None]]


def _f(value: object) -> float:
    if value is None:
        return 0.0
    if isinstance(value, Decimal):
        return float(value)
    return float(value)


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def compute_user_networth_history(
    db: AsyncSession,
    user_id: uuid.UUID,
    *,
    progress: Optional[ProgressCb] = None,
) -> int:
    """Compute & persist the user's real daily net-worth series.

    Returns the number of daily rows written (0 if the user has no positions).
    """
    txns = list(
        (
            await db.execute(
                select(MfTransaction)
                .where(MfTransaction.user_id == user_id)
                .order_by(
                    MfTransaction.transaction_date.asc(), MfTransaction.created_at.asc()
                )
            )
        )
        .scalars()
        .all()
    )
    if not txns:
        return 0

    by_scheme: dict[str, list[MfTransaction]] = {}
    for t in txns:
        by_scheme.setdefault(t.scheme_code, []).append(t)

    today = date.today()
    global_first = min(t.transaction_date for t in txns)
    if global_first > today:
        global_first = today

    # Accumulate per-day totals across all schemes.
    value_by_day: dict[date, float] = {}
    invested_by_day: dict[date, float] = {}

    scheme_codes = list(by_scheme.keys())
    n_schemes = max(1, len(scheme_codes))

    for idx, scheme_code in enumerate(scheme_codes):
        items = by_scheme[scheme_code]
        scheme_first = items[0].transaction_date

        nav_rows = list(
            (
                await db.execute(
                    select(MfNavHistory.nav_date, MfNavHistory.nav)
                    .where(
                        MfNavHistory.scheme_code == scheme_code,
                        MfNavHistory.nav_date <= today,
                    )
                    .order_by(MfNavHistory.nav_date.asc())
                )
            ).all()
        )
        # Sorted (date, nav) for forward-carry lookup.
        navs: list[tuple[date, float]] = [(r[0], _f(r[1])) for r in nav_rows]

        units = 0.0
        invested = 0.0  # net cost basis (buys − sells) of units still held
        last_nav = 0.0
        txn_i = 0
        nav_i = 0
        n_txn = len(items)
        n_nav = len(navs)

        day = scheme_first
        while day <= today:
            # Apply every transaction dated on this day.
            while txn_i < n_txn and items[txn_i].transaction_date == day:
                t = items[txn_i]
                # CAS stores redemption units as a *negative* number. Take the magnitude
                # and let the transaction type decide the sign below — otherwise a SELL's
                # ``units -= (-x)`` would add the units back and inflate the balance.
                t_units = abs(_f(t.units))
                t_amt = abs(_f(t.amount))
                if t.transaction_type in _UNITS_IN_TYPES:
                    units += t_units
                    invested += t_amt
                elif t.transaction_type in _UNITS_OUT_TYPES:
                    # Reduce the cost basis by the *cost* of the units sold (average-cost
                    # method), not by the redemption proceeds — otherwise a profitable
                    # sale would wrongly drag invested below zero and corrupt gain%.
                    if units > 1e-9:
                        invested *= max(0.0, units - t_units) / units
                    units -= t_units
                txn_i += 1

            # Carry the latest published NAV at/behind this day forward.
            while nav_i < n_nav and navs[nav_i][0] <= day:
                last_nav = navs[nav_i][1]
                nav_i += 1

            held_units = units if units > 1e-9 else 0.0
            value = held_units * last_nav
            cost = invested if invested > 0 else 0.0

            value_by_day[day] = value_by_day.get(day, 0.0) + value
            invested_by_day[day] = invested_by_day.get(day, 0.0) + cost

            day += timedelta(days=1)

        if progress is not None:
            await progress((idx + 1) / n_schemes)

    # ── Anchor to the authoritative current portfolio ───────────────────────────
    # The series above is replayed purely from the transaction ledger. When a CAS
    # statement covers only a recent window (or holdings came from a non-ledger
    # source), that ledger is *partial* — it understates both value and cost, while
    # the dashboard headline (and the read-path's "today" point) use the true
    # holdings totals. Left alone, the invested line tops out at the partial-ledger
    # cost and then jumps to the real total at the latest point.
    #
    # Fix: add the pre-window "opening position" — the gap between the authoritative
    # totals and the ledger's own latest day — held flat across the whole window, so
    # the series is continuous and ends exactly at the headline. For a complete
    # ledger the gap is ~0, so nothing changes.
    open_value = 0.0
    open_invested = 0.0
    try:
        pf = await revalue_primary_portfolio_at_latest_nav(db, user_id)
    except Exception:  # noqa: BLE001 — never fail the series on a revalue hiccup
        await db.rollback()
        pf = None
    if pf is not None:
        auth_value = _f(pf.total_value)
        auth_invested = _f(pf.total_invested)
        if auth_invested > 0:
            open_invested = max(0.0, auth_invested - invested_by_day.get(today, 0.0))
        if auth_value > 0:
            open_value = max(0.0, auth_value - value_by_day.get(today, 0.0))

    # Build one row per day across the full window.
    rows: list[UserPortfolioNavHistory] = []
    day = global_first
    while day <= today:
        total = round(value_by_day.get(day, 0.0) + open_value, 2)
        invested = round(invested_by_day.get(day, 0.0) + open_invested, 2)
        gain_pct = (
            round((total - invested) / invested * 100, 4) if invested > 0 else 0.0
        )
        rows.append(
            UserPortfolioNavHistory(
                id=uuid.uuid4(),
                user_id=user_id,
                recorded_date=day,
                total_value=total,
                total_invested=invested,
                gain_percentage=gain_pct,
            )
        )
        day += timedelta(days=1)

    # Replace any prior (synthetic or stale) series for this user.
    await db.execute(
        delete(UserPortfolioNavHistory).where(
            UserPortfolioNavHistory.user_id == user_id
        )
    )
    for start in range(0, len(rows), 1000):
        db.add_all(rows[start : start + 1000])
        await db.flush()
    await db.commit()
    return len(rows)


# ─────────────────────────── Job orchestration ───────────────────────────


async def create_job(db: AsyncSession, user_id: uuid.UUID) -> PortfolioNetworthJob:
    job = PortfolioNetworthJob(
        user_id=user_id, status="pending", phase="queued", progress_pct=0
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)
    return job


async def get_latest_job(
    db: AsyncSession, user_id: uuid.UUID
) -> Optional[PortfolioNetworthJob]:
    return (
        await db.execute(
            select(PortfolioNetworthJob)
            .where(PortfolioNetworthJob.user_id == user_id)
            .order_by(PortfolioNetworthJob.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def has_running_job(
    db: AsyncSession, user_id: uuid.UUID
) -> Optional[PortfolioNetworthJob]:
    return (
        await db.execute(
            select(PortfolioNetworthJob)
            .where(
                PortfolioNetworthJob.user_id == user_id,
                PortfolioNetworthJob.status.in_(("pending", "running")),
            )
            .order_by(PortfolioNetworthJob.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def _update_job(db: AsyncSession, job_id: uuid.UUID, **fields: object) -> None:
    fields["updated_at"] = func.now()
    await db.execute(
        update(PortfolioNetworthJob)
        .where(PortfolioNetworthJob.id == job_id)
        .values(**fields)
    )
    await db.commit()


async def run_networth_backfill(user_id: uuid.UUID, job_id: uuid.UUID) -> None:
    """Background entrypoint: fetch NAV history (Phase A) then compute (Phase B).

    Runs on a fresh session because the request-scoped one is closed by the time
    a BackgroundTasks callback fires.
    """
    factory = _get_session_factory()
    async with factory() as db:
        try:
            await _update_job(
                db,
                job_id,
                status="running",
                phase="fetching_nav",
                progress_pct=1,
                started_at=_now(),
                message="Preparing your statement data…",
            )

            txns = list(
                (
                    await db.execute(
                        select(
                            MfTransaction.scheme_code,
                            func.min(MfTransaction.transaction_date),
                        )
                        .where(MfTransaction.user_id == user_id)
                        .group_by(MfTransaction.scheme_code)
                    )
                ).all()
            )
            if not txns:
                await _update_job(
                    db,
                    job_id,
                    status="success",
                    phase="done",
                    progress_pct=100,
                    finished_at=_now(),
                    message="No transactions to build history from.",
                )
                return

            today = date.today()
            global_first = min(first for _, first in txns)
            await _update_job(
                db,
                job_id,
                history_from=global_first,
                days_total=int((today - global_first).days) + 1,
            )

            # Phase A — ensure each fund's NAV is stored back to its first txn date.
            n = len(txns)
            for i, (scheme_code, first) in enumerate(txns):
                try:
                    # ``first`` is the fund's first transaction date, so it was held
                    # from then — require NAV history back to it (closes the leading
                    # gap left by daily latest-NAV top-ups, which otherwise zeroes the
                    # fund's value for every month before the stored window).
                    await ensure_nav_history_for_chart(
                        db,
                        scheme_code,
                        date_from=first,
                        date_to=today,
                        require_from_start=True,
                    )
                except Exception:  # noqa: BLE001 — best effort per scheme
                    logger.exception(
                        "networth backfill: NAV fetch failed for scheme %s", scheme_code
                    )
                pct = 2 + ((i + 1) / n) * 48  # 2 → 50%
                await _update_job(
                    db,
                    job_id,
                    progress_pct=round(pct, 2),
                    message=f"Fetching NAV history… {i + 1}/{n} funds",
                )

            # Phase B — compute the real daily series.
            await _update_job(
                db,
                job_id,
                phase="computing",
                progress_pct=50,
                message="Calculating daily net worth…",
            )

            async def _prog(frac: float) -> None:
                await _update_job(
                    db,
                    job_id,
                    progress_pct=round(50 + frac * 48, 2),
                    message="Calculating daily net worth…",
                )

            written = await compute_user_networth_history(db, user_id, progress=_prog)

            await _update_job(
                db,
                job_id,
                status="success",
                phase="done",
                progress_pct=100,
                finished_at=_now(),
                message=f"Built {written} days of net-worth history.",
            )
        except Exception as exc:  # noqa: BLE001 — surface failure to the poller
            logger.exception("networth backfill job %s failed", job_id)
            try:
                await _update_job(
                    db,
                    job_id,
                    status="failed",
                    finished_at=_now(),
                    message=f"Failed: {exc}"[:300],
                )
            except Exception:  # noqa: BLE001
                logger.exception("could not mark networth job %s failed", job_id)


# ──────────────────────────── Daily scheduler ────────────────────────────

NETWORTH_LOCK_KEY = 7421101


async def _latest_nav_on_or_before(
    db: AsyncSession, scheme_code: str, on_day: date
) -> Optional[float]:
    # ``scheme_code`` may actually be an ISIN (CAS ingest stores whichever it could
    # resolve). mf_nav_history is keyed by AMFI scheme code but also carries the ISIN, so
    # match on either — otherwise ISIN-keyed holdings (e.g. INF666M01LC7) never price.
    return (
        await db.execute(
            select(MfNavHistory.nav)
            .where(
                or_(
                    MfNavHistory.scheme_code == scheme_code,
                    func.upper(MfNavHistory.isin) == scheme_code.upper(),
                ),
                MfNavHistory.nav_date <= on_day,
            )
            .order_by(MfNavHistory.nav_date.desc())
            .limit(1)
        )
    ).scalar()


async def compute_networth_as_of(
    db: AsyncSession, user_id: uuid.UUID, as_of: date
) -> Optional[tuple[float, float, float]]:
    """``(total_value, total_invested, gain_pct)`` valued *as of* ``as_of`` from the ledger.

    Replays every transaction dated on/before ``as_of`` to get held units per scheme,
    then values each at the most recent stored NAV on/before ``as_of`` — carried forward
    across non-trading days, so the value is well-defined on weekends/holidays too. Cost
    basis uses the average-cost method so a profitable redemption can't push invested
    negative. Returns ``None`` when the user has no transactions on/before ``as_of``.
    """
    txns = list(
        (
            await db.execute(
                select(MfTransaction)
                .where(
                    MfTransaction.user_id == user_id,
                    MfTransaction.transaction_date <= as_of,
                )
                .order_by(
                    MfTransaction.transaction_date.asc(), MfTransaction.created_at.asc()
                )
            )
        )
        .scalars()
        .all()
    )
    if not txns:
        return None

    # Running unit balance and remaining cost basis per scheme, in chronological order.
    units_by: dict[str, float] = {}
    invested_by: dict[str, float] = {}
    for t in txns:
        # CAS stores redemption units as a *negative* number. Use the magnitude and let
        # the transaction type drive the sign — ``held - (-x)`` would otherwise add the
        # redeemed units back and over-count the balance (the 1.33cr-vs-1.07cr bug).
        u = abs(_f(t.units) or 0.0)
        amt = abs(_f(t.amount) or 0.0)
        sc = t.scheme_code
        if t.transaction_type in _UNITS_IN_TYPES:
            units_by[sc] = units_by.get(sc, 0.0) + u
            invested_by[sc] = invested_by.get(sc, 0.0) + amt
        elif t.transaction_type in _UNITS_OUT_TYPES:
            held = units_by.get(sc, 0.0)
            if held > 1e-9:
                # Drop cost basis in proportion to units sold (average-cost method).
                invested_by[sc] = invested_by.get(sc, 0.0) * max(0.0, held - u) / held
            units_by[sc] = held - u

    total_value = 0.0
    total_invested = 0.0
    for scheme_code, units in units_by.items():
        invested = invested_by.get(scheme_code, 0.0)
        if units <= 1e-9:
            continue
        if invested > 0:
            total_invested += invested
        nav = await _latest_nav_on_or_before(db, scheme_code, as_of)
        if nav is not None:
            total_value += units * float(nav)

    total_value = round(total_value, 2)
    total_invested = round(total_invested, 2)
    gain_pct = (
        round((total_value - total_invested) / total_invested * 100, 4)
        if total_invested > 0
        else 0.0
    )
    return total_value, total_invested, gain_pct


async def compute_today_networth(
    db: AsyncSession, user_id: uuid.UUID
) -> Optional[tuple[float, float, float]]:
    """Today's ``(total_value, total_invested, gain_pct)`` — see ``compute_networth_as_of``.

    Single source of truth for "today's net worth" from the transaction ledger: held
    units per scheme valued at the most recent stored NAV (carried forward, no external
    fetch). Returns ``None`` when the user has no transactions.
    """
    return await compute_networth_as_of(db, user_id, date.today())


async def append_today_networth(db: AsyncSession, user_id: uuid.UUID) -> bool:
    """Upsert just today's net-worth point for one user (cheap daily path).

    Uses the latest stored NAV per scheme (the 00:05 IST job has already refreshed
    it by the time this runs), so it does not reach out to mfapi.in.
    """
    computed = await compute_today_networth(db, user_id)
    if computed is None:
        return False
    total_value, total_invested, gain_pct = computed

    today = date.today()
    row = {
        "id": uuid.uuid4(),
        "user_id": user_id,
        "recorded_date": today,
        "total_value": total_value,
        "total_invested": total_invested,
        "gain_percentage": gain_pct,
    }
    dialect = db.bind.dialect.name if db.bind is not None else "postgresql"
    insert = pg_insert if dialect == "postgresql" else sqlite_insert
    stmt = insert(UserPortfolioNavHistory).values(row)
    stmt = stmt.on_conflict_do_update(
        index_elements=["user_id", "recorded_date"],
        set_={
            "total_value": stmt.excluded.total_value,
            "total_invested": stmt.excluded.total_invested,
            "gain_percentage": stmt.excluded.gain_percentage,
        },
    )
    await db.execute(stmt)
    await db.commit()
    return True


# How long an outage we'll patch by forward-carrying the latest value before falling
# back to a full per-day recompute. A weekend + a holiday is at most ~4 days; the job
# runs several times a day, so in practice the trailing gap is 0–1 day.
_MAX_FORWARD_FILL_DAYS = 7


async def _last_recorded_date(db: AsyncSession, user_id: uuid.UUID) -> Optional[date]:
    return (
        await db.execute(
            select(func.max(UserPortfolioNavHistory.recorded_date)).where(
                UserPortfolioNavHistory.user_id == user_id
            )
        )
    ).scalar()


async def _upsert_nav_points(
    db: AsyncSession,
    user_id: uuid.UUID,
    points: list[tuple[date, tuple[float, float, float]]],
) -> None:
    """Idempotently upsert one row per (user, day). ``points`` = [(day, (value, invested, gain))]."""
    if not points:
        return
    rows = [
        {
            "id": uuid.uuid4(),
            "user_id": user_id,
            "recorded_date": day,
            "total_value": round(value, 2),
            "total_invested": round(invested, 2),
            "gain_percentage": round(gain, 4),
        }
        for day, (value, invested, gain) in points
    ]
    dialect = db.bind.dialect.name if db.bind is not None else "postgresql"
    insert = pg_insert if dialect == "postgresql" else sqlite_insert
    stmt = insert(UserPortfolioNavHistory).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=["user_id", "recorded_date"],
        set_={
            "total_value": stmt.excluded.total_value,
            "total_invested": stmt.excluded.total_invested,
            "gain_percentage": stmt.excluded.gain_percentage,
        },
    )
    await db.execute(stmt)
    await db.commit()


async def _authoritative_today_value(
    db: AsyncSession, user_id: uuid.UUID
) -> Optional[tuple[float, float, float]]:
    """Today's value exactly as the dashboard headline shows it.

    The headline is the holdings roll-up re-marked to today's NAV
    (``revalue_primary_portfolio_at_latest_nav``) — the single authoritative figure.
    We fall back to the transaction ledger only when there are no priced holdings
    (e.g. transactions imported without a CAS holdings snapshot). Sourcing the chart's
    latest point from this same value is what keeps chart-latest == portfolio value.
    """
    try:
        portfolio = await revalue_primary_portfolio_at_latest_nav(db, user_id)
    except Exception:  # noqa: BLE001 — never fail the series on a NAV lookup
        await db.rollback()
        portfolio = None
    if portfolio is not None:
        total_value = _f(portfolio.total_value)
        if total_value > 0:
            total_invested = _f(portfolio.total_invested)
            gain_pct = (
                round((total_value - total_invested) / total_invested * 100, 4)
                if total_invested > 0
                else 0.0
            )
            return total_value, total_invested, gain_pct
    return await compute_today_networth(db, user_id)


async def ensure_history_current_through_today(
    db: AsyncSession, user_id: uuid.UUID, *, allow_full_rebuild: bool = True
) -> bool:
    """Guarantee the user's net-worth series runs through *today*, holiday or not.

    Two invariants this enforces on every call:

    1. **Yesterday is always present** — non-trading days never publish a NAV, so the
       daily job may not have a fresh point for them. We back-fill every missing
       trailing day by valuing it at that day's carried-forward NAV, so the chart
       always has a point for yesterday (and every day up to today) regardless of
       weekends/holidays.
    2. **Chart-latest == dashboard headline** — today's point is (re)written from the
       authoritative holdings roll-up, so the chart's last value can never drift away
       from the portfolio value shown elsewhere.

    Returns ``True`` if any row was written. No-ops (``False``) when the user has no
    series yet — the first build is owned by the backfill CTA, not this top-up.
    """
    today = date.today()
    last = await _last_recorded_date(db, user_id)
    if last is None:
        return False

    authoritative = await _authoritative_today_value(db, user_id)
    if authoritative is None or authoritative[0] <= 0:
        return False

    points: list[tuple[date, tuple[float, float, float]]] = []
    if last < today:
        gap_days = (today - last).days
        if gap_days > _MAX_FORWARD_FILL_DAYS and allow_full_rebuild:
            # A real outage (job down for days): recompute every day correctly in one
            # pass rather than forward-carrying a single value across a long window.
            await compute_user_networth_history(db, user_id)
            await _upsert_nav_points(db, user_id, [(today, authoritative)])
            return True
        # Earliest day we'll fill. On the read path we keep it cheap by only restoring
        # the recent trailing window; the scheduled job repairs any older hole.
        first_fill = (
            last + timedelta(days=1)
            if gap_days <= _MAX_FORWARD_FILL_DAYS
            else today - timedelta(days=_MAX_FORWARD_FILL_DAYS)
        )
        day = first_fill
        while day < today:
            valued = await compute_networth_as_of(db, user_id, day)
            if valued is not None:
                points.append((day, valued))
            day += timedelta(days=1)

    # Always (re)write today from the authoritative headline so chart-latest matches it.
    points.append((today, authoritative))
    await _upsert_nav_points(db, user_id, points)
    return True


async def _refresh_held_fund_navs(db: AsyncSession) -> None:
    """Pull the latest NAV from mfapi.in for every *held* scheme still missing today's NAV.

    Run several times a day, this is what lets late-publishing ("bottleneck") funds
    surface their newest NAV without re-fetching the whole ~8k-scheme universe each
    time — we only touch the funds users actually hold that don't yet have today's NAV.
    Best-effort: a fetch failure must never block the net-worth refresh below.
    """
    try:
        from app.domains.mutual_funds.services.mfapi_ingest_service import (
            IngestMode,
            ingest_mfapi,
            list_scheme_codes_needing_nav_refresh,
        )

        held_rows = (
            (await db.execute(select(MfTransaction.scheme_code).distinct()))
            .scalars()
            .all()
        )
        held = {str(c).strip() for c in held_rows if c and str(c).strip()}
        if not held:
            return
        # ``min_nav_date=today`` flags schemes whose latest NAV is older than today —
        # i.e. those that have not yet published today's NAV (the bottleneck funds).
        stale_codes, _ = await list_scheme_codes_needing_nav_refresh(
            db, min_nav_date=date.today()
        )
        to_refresh = [c for c in stale_codes if c in held]
        if not to_refresh:
            logger.info(
                "networth daily job: all %d held funds already have today's NAV",
                len(held),
            )
            return
        logger.info(
            "networth daily job: refreshing NAV for %d/%d held funds missing today's NAV",
            len(to_refresh),
            len(held),
        )
        await ingest_mfapi(
            db, mode=IngestMode.INCREMENTAL, scheme_codes=to_refresh, concurrency=8
        )
    except Exception:  # noqa: BLE001 — NAV refresh is best-effort
        logger.exception(
            "networth daily job: held-fund NAV refresh failed (continuing)"
        )
        try:
            await db.rollback()
        except Exception:  # noqa: BLE001
            pass


@traced_job("networth.daily_job")
async def run_daily_networth_job() -> None:
    """Refresh held-fund NAV, then bring every user's net-worth series through today.

    Registered to run several times a day (after each NAV pull). Serialized across
    uvicorn workers via a Postgres advisory lock. For each user this both back-fills
    any missing trailing day (so the chart always shows yesterday, holiday or not) and
    reconciles today's point to the dashboard headline (so the two never disagree).
    """
    logger.info("networth daily job: starting")
    factory = _get_session_factory()
    try:
        async with factory() as db:
            got_lock = (
                await db.execute(
                    text("SELECT pg_try_advisory_lock(:k)"), {"k": NETWORTH_LOCK_KEY}
                )
            ).scalar()
            if not got_lock:
                logger.info("networth daily job: lock held by another worker; skipping")
                return
            try:
                # Catch late-publishing ("bottleneck") funds before we revalue anyone.
                await _refresh_held_fund_navs(db)

                user_ids = list(
                    (await db.execute(select(MfTransaction.user_id).distinct()))
                    .scalars()
                    .all()
                )
                done = 0
                for user_id in user_ids:
                    try:
                        if await ensure_history_current_through_today(
                            db, user_id, allow_full_rebuild=True
                        ):
                            done += 1
                    except Exception:  # noqa: BLE001 — never let one user block the rest
                        logger.exception(
                            "networth daily job: failed for user %s", user_id
                        )
                        try:
                            await db.rollback()
                        except Exception:  # noqa: BLE001
                            pass
                logger.info(
                    "networth daily job: refreshed net-worth series for %d/%d users",
                    done,
                    len(user_ids),
                )
                # The per-user handler above swallows failures so one user can't
                # block the rest, which means the run reports success even when
                # most users failed. These counts are what expose that.
                record_job_counts(
                    users_total=len(user_ids),
                    users_refreshed=done,
                    users_failed=len(user_ids) - done,
                )
            finally:
                try:
                    await db.execute(
                        text("SELECT pg_advisory_unlock(:k)"), {"k": NETWORTH_LOCK_KEY}
                    )
                except SQLAlchemyError:
                    logger.warning(
                        "networth daily job: failed to release advisory lock",
                        exc_info=True,
                    )
    except SQLAlchemyError:
        logger.warning(
            "networth daily job: database unavailable; will retry next schedule",
            exc_info=True,
        )
