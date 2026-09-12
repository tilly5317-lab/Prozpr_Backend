"""The daily net-worth refresh — one statement for the whole fleet, not a loop per user.

The old shape walked every user on one shared session, calling a per-user helper that
issued several queries and could trigger a *full rebuild* inline. At a few hundred users
that is merely slow; at ten thousand it will not finish between cron ticks, and one
user's failure poisons the session everyone else is sharing.

What makes it cheap is a single observation: **``total_invested`` only changes when a
transaction happens, and transactions only arrive via a CAS upload — which already
triggers a full rebuild.** So the daily pass never needs to replay anything. It re-prices
the units the user holds at the latest NAV and carries the cost basis forward from
``user_networth_series_state.last_invested``. That collapses to one set-based upsert.

Two safety properties are structural rather than remembered:

* It reads ``user_scheme_position``, never ``mf_transactions``. The ledger is
  ``CasScoped`` and the CAS read hook only filters ORM ``SELECT``s — a fleet-wide raw
  aggregate over it would silently double-count every user who has uploaded twice.
  Positions are materialised once, under scope, by the rebuild.
* It skips any user with a live rebuild rather than racing it, so the two writers never
  disagree about the same day.

Both inputs — positions and state — are written only by a rebuild, so a user who has
never been rebuilt is invisible to the pricing pass. ``_queue_rebuilds`` closes that
hole by adopting them (see ``_STALE_USERS``); without it every account that predates
this module keeps whatever the old builder last wrote, forever.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, timedelta

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import _get_session_factory
from app.core.job_tracing import (
    job_span,
    record_job_counts,
    report_job_failure,
    traced_job,
)
from app.domains.portfolio.services.networth import job as job_store
from app.domains.portfolio.services.networth.clock import ist_today

logger = logging.getLogger(__name__)

# Serialises the daily job across uvicorn workers.
NETWORTH_DAILY_LOCK_KEY = 7421102

# How long an outage we will patch by re-pricing each missing day. Beyond this the user
# gets a real rebuild instead of a week of back-filled points.
MAX_FORWARD_FILL_DAYS = 7

# Users per run that may be queued for a full rebuild because they fell too far behind.
# Bounded so a bad week cannot turn one cron tick into a fleet-wide recompute.
MAX_REBUILDS_PER_RUN = 50

# Chart-latest and the dashboard headline must agree. Both derive from held units x
# latest NAV, so they agree by construction — this is the tripwire for when they do not.
RECONCILE_TOLERANCE = 0.005


# One statement, whole fleet. LATERAL + uq_mf_nav_scheme_date makes the price lookup an
# index scan per position; users with a live rebuild, no positions, or no series state
# simply produce no row.
_UPSERT_DAY = text(
    """
    WITH priced AS (
        SELECT p.user_id,
               SUM(p.units * n.nav) AS total_value
        FROM   user_scheme_position p
        JOIN   LATERAL (
                   SELECT m.nav
                   FROM   mf_nav_history m
                   WHERE  m.scheme_code = p.nav_key
                     AND  m.nav_date <= :as_of
                     AND  m.nav > 0
                   ORDER  BY m.nav_date DESC
                   LIMIT  1
               ) n ON TRUE
        WHERE  p.units > 0.000001
          AND  p.nav_key IS NOT NULL
        GROUP  BY p.user_id
    )
    INSERT INTO user_portfolio_nav_history
          (id, user_id, recorded_date, total_value, total_invested, gain_percentage)
    SELECT gen_random_uuid(), pr.user_id, :as_of,
           LEAST(pr.total_value, 9999999999999999.99),
           s.last_invested,
           CASE WHEN s.last_invested > 0
                THEN GREATEST(LEAST(
                       round((pr.total_value - s.last_invested)
                             / s.last_invested * 100, 4),
                       999999.9999), -999999.9999)
                ELSE 0 END
    FROM   priced pr
    JOIN   user_networth_series_state s ON s.user_id = pr.user_id
    LEFT   JOIN portfolio_networth_jobs j
           ON j.user_id = pr.user_id AND j.status IN ('pending', 'running')
    WHERE  j.id IS NULL
      AND  s.first_date IS NOT NULL
      AND  s.first_date <= :as_of
    ON CONFLICT (user_id, recorded_date) DO UPDATE
    SET total_value = EXCLUDED.total_value,
        total_invested = EXCLUDED.total_invested,
        gain_percentage = EXCLUDED.gain_percentage,
        updated_at = now()
    """
)

_ADVANCE_STATE = text(
    """
    UPDATE user_networth_series_state s
    SET    last_date = :as_of, updated_at = now()
    FROM   user_portfolio_nav_history h
    WHERE  h.user_id = s.user_id
      AND  h.recorded_date = :as_of
      AND  (s.last_date IS NULL OR s.last_date < :as_of)
    """
)

_STALE_USERS = text(
    """
    WITH candidate AS (
        SELECT s.user_id, s.last_date
        FROM   user_networth_series_state s
        WHERE  s.first_date IS NOT NULL
          AND  (s.last_date IS NULL OR s.last_date < :cutoff)

        UNION ALL

        -- Users whose series predates this table. Every statement in this module is
        -- keyed on state and positions, both of which only a rebuild writes — so a
        -- user carrying history from the old builder is priced by nothing and
        -- queued by nothing, and their chart silently freezes on whatever the old
        -- job last wrote. One rebuild adopts them; after that the branch above has
        -- them. This is the whole migration path, and it must not be removed until
        -- no user is left without a state row.
        SELECT h.user_id, max(h.recorded_date) AS last_date
        FROM   user_portfolio_nav_history h
        LEFT   JOIN user_networth_series_state s2 ON s2.user_id = h.user_id
        WHERE  s2.user_id IS NULL
        GROUP  BY h.user_id
    )
    SELECT c.user_id
    FROM   candidate c
    LEFT   JOIN portfolio_networth_jobs j
           ON j.user_id = c.user_id AND j.status IN ('pending', 'running')
    WHERE  j.id IS NULL
    ORDER  BY c.last_date NULLS FIRST
    LIMIT  :limit
    """
)

_RECONCILE = text(
    """
    SELECT h.user_id, h.total_value AS series_value, p.total_value AS headline_value
    FROM   user_portfolio_nav_history h
    JOIN   portfolios p ON p.user_id = h.user_id AND p.is_primary = TRUE
    WHERE  h.recorded_date = :as_of
      AND  p.total_value > 0
      AND  abs(h.total_value - p.total_value) > p.total_value * :tolerance
    LIMIT  50
    """
)


async def refresh_day(db: AsyncSession, as_of: date) -> int:
    """Write every user's net-worth point for ``as_of``. Returns rows affected."""
    result = await db.execute(_UPSERT_DAY, {"as_of": as_of})
    written = int(result.rowcount or 0)
    await db.execute(_ADVANCE_STATE, {"as_of": as_of})
    await db.commit()
    return written


async def _refresh_held_fund_navs(db: AsyncSession) -> None:
    """Pull the latest NAV for every *held* fund that has not published today's yet.

    Run several times a day, this is what lets late-publishing ("bottleneck") funds
    surface their newest NAV without re-fetching the whole ~8k-scheme universe each
    time. The fund set comes from ``user_scheme_position`` — smaller than the ledger,
    already deduplicated, already resolved to the code that actually prices, and
    scope-safe by construction. Best-effort: a fetch failure must never block the
    refresh below.
    """
    try:
        from app.domains.mutual_funds.services.mfapi_ingest_service import (
            IngestMode,
            ingest_mfapi,
            list_scheme_codes_needing_nav_refresh,
        )

        held_rows = (
            (
                await db.execute(
                    text(
                        "SELECT DISTINCT nav_key FROM user_scheme_position "
                        "WHERE nav_key IS NOT NULL AND units > 0.000001"
                    )
                )
            )
            .scalars()
            .all()
        )
        held = {str(code).strip() for code in held_rows if code and str(code).strip()}
        if not held:
            return
        # ``min_nav_date=today`` flags schemes whose latest NAV is older than today —
        # i.e. those that have not yet published (the bottleneck funds).
        stale_codes, _ = await list_scheme_codes_needing_nav_refresh(
            db, min_nav_date=ist_today()
        )
        to_refresh = [code for code in stale_codes if code in held]
        if not to_refresh:
            logger.info(
                "networth daily: all %d held funds already have today's NAV", len(held)
            )
            return
        logger.info(
            "networth daily: refreshing NAV for %d/%d held funds missing today's NAV",
            len(to_refresh),
            len(held),
        )
        await ingest_mfapi(
            db, mode=IngestMode.INCREMENTAL, scheme_codes=to_refresh, concurrency=8
        )
    except Exception as exc:  # noqa: BLE001 — NAV refresh is best-effort
        logger.exception("networth daily: held-fund NAV refresh failed (continuing)")
        report_job_failure(exc, job="networth.daily_job", phase="nav_refresh")
        try:
            await db.rollback()
        except Exception:  # noqa: BLE001
            pass


def _consume_task_exception(task: asyncio.Task) -> None:
    """Swallow a fire-and-forget rebuild's exception; the job row already has it."""
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.warning("networth daily: queued rebuild failed: %r", exc)


async def _queue_rebuilds(db: AsyncSession, cutoff: date) -> int:
    """Queue a real rebuild for users this pass cannot price correctly itself.

    Two kinds, and the second is the one that is easy to forget:

    * Too far behind to forward-fill honestly — carrying one value flat across a month
      is not history, it is a straight line, so past ``MAX_FORWARD_FILL_DAYS`` we
      recompute properly instead.
    * Never rebuilt since positions and state became the inputs. Those users have a
      series and nothing else, so the priced upsert above skips them forever; a rebuild
      is what adopts them into the new shape.
    """
    from app.domains.portfolio.services.networth.builder import rebuild_user_networth

    rows = (
        (
            await db.execute(
                _STALE_USERS, {"cutoff": cutoff, "limit": MAX_REBUILDS_PER_RUN}
            )
        )
        .scalars()
        .all()
    )
    queued = 0
    for user_id in rows:
        try:
            job, created = await job_store.create_job(
                db, user_id, trigger="daily", supersede=False
            )
            if created:
                # Fire and forget: one user's rebuild must never hold up the sweep.
                # The done-callback consumes the exception so a failed rebuild does
                # not surface as "Task exception was never retrieved" — the job row
                # already records the failure, and the sweep must carry on.
                task = asyncio.create_task(
                    rebuild_user_networth(user_id, job.id, trigger="daily")
                )
                task.add_done_callback(_consume_task_exception)
                queued += 1
        except Exception:  # noqa: BLE001 — never let one user block the rest
            logger.exception("networth daily: could not queue rebuild for %s", user_id)
            try:
                await db.rollback()
            except Exception:  # noqa: BLE001
                pass
    return queued


async def _reconcile(db: AsyncSession, as_of: date) -> int:
    """Log users whose chart-latest has drifted from the dashboard headline.

    The old code *rewrote* today's point from the holdings roll-up for every user, every
    run, to force the two to match. Under the position-based refresh both sides derive
    from the same held units x latest NAV, so they agree by construction — which makes a
    disagreement a signal worth seeing rather than a nightly write storm worth hiding.
    """
    rows = (
        await db.execute(_RECONCILE, {"as_of": as_of, "tolerance": RECONCILE_TOLERANCE})
    ).all()
    for row in rows:
        logger.warning(
            "networth daily: user %s series value %s != portfolio headline %s on %s",
            row.user_id,
            row.series_value,
            row.headline_value,
            as_of,
        )
    return len(rows)


@traced_job("networth.daily_job")
async def run_daily_networth_job() -> None:
    """Refresh held-fund NAV, then bring every user's series through today.

    Registered to run several times a day, just after each NAV pull. Serialised across
    uvicorn workers via a Postgres advisory lock.
    """
    logger.info("networth daily job: starting")
    factory = _get_session_factory()
    try:
        async with factory() as db:
            got_lock = (
                await db.execute(
                    text("SELECT pg_try_advisory_lock(:k)"),
                    {"k": NETWORTH_DAILY_LOCK_KEY},
                )
            ).scalar()
            if not got_lock:
                logger.info("networth daily job: lock held by another worker; skipping")
                return
            try:
                # Close out builds whose worker stopped existing (deploy, OOM). Nothing
                # in-process can do this, and until it happens the dashboard polls a job
                # that will never move and the single-flight guard blocks the rebuild
                # that would fix the account.
                reaped = await job_store.reap_stale_jobs(db)

                with job_span("networth.daily.nav_refresh"):
                    await _refresh_held_fund_navs(db)

                today = ist_today()
                # Back-fill every missing trailing day, then today. Each day is still
                # ONE statement for the whole fleet, so a week-long outage costs seven
                # statements — not seven times the user count.
                with job_span("networth.daily.price"):
                    written_today = 0
                    for offset in range(MAX_FORWARD_FILL_DAYS, -1, -1):
                        as_of = today - timedelta(days=offset)
                        written = await refresh_day(db, as_of)
                        if offset == 0:
                            written_today = written

                with job_span("networth.daily.rebuilds"):
                    cutoff = today - timedelta(days=MAX_FORWARD_FILL_DAYS)
                    queued = await _queue_rebuilds(db, cutoff)

                drifted = await _reconcile(db, today)

                logger.info(
                    "networth daily job: priced %d users for %s "
                    "(reaped %d, queued %d rebuilds, %d drifted)",
                    written_today,
                    today,
                    reaped,
                    queued,
                    drifted,
                )
                record_job_counts(
                    users_priced=written_today,
                    jobs_reaped=reaped,
                    rebuilds_queued=queued,
                    users_drifted=drifted,
                )
            finally:
                try:
                    await db.execute(
                        text("SELECT pg_advisory_unlock(:k)"),
                        {"k": NETWORTH_DAILY_LOCK_KEY},
                    )
                except SQLAlchemyError:
                    logger.warning(
                        "networth daily job: failed to release advisory lock",
                        exc_info=True,
                    )
    except SQLAlchemyError as exc:
        logger.warning(
            "networth daily job: database unavailable; will retry next schedule",
            exc_info=True,
        )
        report_job_failure(exc, job="networth.daily_job")
