"""Orchestrates one user's net-worth rebuild: pin scope, close NAV gaps, replay, replace.

The phases and their progress bands are not decoration. Progress is a promise about
*work*, not a number that climbs while the user waits: an earlier version mapped the
whole compute onto the per-scheme loop, so it hit 98% the moment the last scheme was
replayed and then sat there through the portfolio revalue, the row build and the write —
every one of which can be slow, and one of which was reliably fatal. Every wedged job we
ever found read exactly 98.00, which told us only that the loop had ended. Giving each
real step its own band means a stall now names the step it stalled in.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cas_scope import effective_scope, scoped_to
from app.core.database import _get_session_factory
from app.core.job_tracing import (
    job_span,
    record_job_counts,
    report_job_failure,
    traced_job,
)
from app.domains.portfolio.services.networth import job as job_store
from app.domains.portfolio.services.networth import nav_coverage
from app.domains.portfolio.services.networth.clock import ist_today, utc_now
from app.domains.portfolio.services.networth.loader import load_ledger, load_navs
from app.domains.portfolio.services.networth.replay import (
    ZERO,
    Opening,
    SchemeSeries,
    combine,
    replay_scheme,
)
from app.domains.portfolio.services.networth.writer import (
    PositionRow,
    SeriesState,
    clear_series,
    replace_series,
)

logger = logging.getLogger(__name__)

# Each real step owns a range, so a stall names its step.
PCT_LOAD = (2.0, 8.0)
PCT_NAV_FETCH = (8.0, 55.0)
PCT_REPLAY = (55.0, 88.0)
PCT_ANCHOR = 92.0
PCT_PERSIST = 96.0

# How far the ledger may fall short of the authoritative holdings total before we stop
# calling the difference "an opening position" and start calling it a warning.
ANCHOR_WARN_FRACTION = Decimal("0.05")


@dataclass
class RebuildResult:
    days_written: int
    schemes: int
    degraded_schemes: int
    ledger_complete: bool
    warnings: dict[str, int]


@traced_job("networth.rebuild")
async def rebuild_user_networth(
    user_id: uuid.UUID, job_id: uuid.UUID, *, trigger: str = "manual"
) -> None:
    """Background entrypoint. Pins the CAS snapshot, then runs the build.

    A ``BackgroundTasks`` callback carries no request scope. Without pinning here, every
    read below would span every statement the user has ever uploaded at once and the
    series would be built from double-counted units.
    """
    factory = _get_session_factory()
    async with factory() as scope_db:
        snapshot_id = await effective_scope(scope_db, user_id)
    with scoped_to(snapshot_id):
        await _run(user_id, job_id, snapshot_id, trigger)


async def _run(
    user_id: uuid.UUID,
    job_id: uuid.UUID,
    snapshot_id: Optional[uuid.UUID],
    trigger: str,
) -> None:
    """Run the build, then run it again if fresher data landed while we worked.

    Runs on a fresh session because the request-scoped one is closed by the time a
    background callback fires. Job status is written through ``job_store.update_job``,
    which uses a session of its own — so a compute failure that poisons ``db`` can still
    be recorded rather than leaving the row ``running`` forever.
    """
    factory = _get_session_factory()
    async with factory() as db:
        try:
            while True:
                await job_store.update_job(
                    job_id,
                    status="running",
                    phase="loading",
                    progress_pct=PCT_LOAD[0],
                    started_at=utc_now(),
                    message="Preparing your statement data...",
                    trigger=trigger,
                )
                result = await build_series(db, user_id, snapshot_id, job_id=job_id)

                # A second CAS may have landed while this build was running. Joining an
                # in-flight job is right for a double tap on Rebuild and wrong for fresh
                # data — without this the user's chart would reflect the statement they
                # just replaced, with no job left to fix it.
                if await job_store.consume_supersede(job_id):
                    logger.info(
                        "networth job %s: fresher data landed, rebuilding", job_id
                    )
                    continue
                break

            await job_store.update_job(
                job_id,
                status="success",
                phase="done",
                progress_pct=100,
                finished_at=utc_now(),
                warnings=result.warnings or None,
                message=(
                    f"Built {result.days_written} days of net-worth history."
                    if result.days_written
                    else "No transactions to build history from."
                ),
            )
            record_job_counts(
                days_written=result.days_written,
                schemes=result.schemes,
                degraded_schemes=result.degraded_schemes,
            )
        except BaseException as exc:  # noqa: BLE001 — surface EVERY exit to the poller
            # BaseException, not Exception, on purpose. A worker restart cancels this
            # task with ``asyncio.CancelledError``, which derives from BaseException and
            # sails straight past an ``except Exception`` — leaving the job ``running``
            # with nobody left to finish it. Record the outcome and re-raise so
            # cancellation still propagates and the event loop can shut down.
            cancelled = isinstance(exc, asyncio.CancelledError)
            if cancelled:
                logger.warning("networth rebuild job %s cancelled", job_id)
            else:
                logger.exception("networth rebuild job %s failed", job_id)
                # Schedulers and BackgroundTasks catch their own exceptions, so the
                # job span never sees this — without an explicit report a crashed
                # build stays green in the trace and files no issue.
                report_job_failure(exc, job="networth.rebuild")
            # The data session is very likely inside an aborted transaction. Roll it
            # back so the context manager gets it clean; the status write below does
            # not depend on it either way — that is the whole point of update_job
            # owning its own session.
            try:
                await db.rollback()
            except Exception:  # noqa: BLE001
                logger.debug("rollback after rebuild failure failed", exc_info=True)
            try:
                await job_store.update_job(
                    job_id,
                    status="failed",
                    finished_at=utc_now(),
                    message=(
                        "Build was interrupted. Tap to try again."
                        if cancelled
                        else f"Failed: {exc}"[:300]
                    ),
                )
            except Exception:  # noqa: BLE001
                logger.exception("could not mark networth job %s failed", job_id)
            raise


async def build_series(
    db: AsyncSession,
    user_id: uuid.UUID,
    snapshot_id: Optional[uuid.UUID] = None,
    *,
    job_id: Optional[uuid.UUID] = None,
) -> RebuildResult:
    """Compute the user's whole series and atomically replace what is stored.

    Callers are responsible for CAS scope; this reads whatever scope is active.
    """
    today = ist_today()

    async def progress(pct: float, message: str, **fields: object) -> None:
        if job_id is not None:
            await job_store.update_job(
                job_id, progress_pct=round(pct, 2), message=message, **fields
            )

    with job_span("networth.load"):
        ledger = await load_ledger(db, user_id, today)

    if ledger.is_empty:
        # No positions at all. Clearing rather than returning early matters: a user
        # whose ledger disappeared (a corrected re-upload, a reset) would otherwise
        # keep a ghost chart forever, priced off holdings that no longer exist.
        await clear_series(db, user_id)
        return RebuildResult(0, 0, 0, True, {})

    # ── Phase A: close NAV gaps ──────────────────────────────────────────────
    nav_key_first_txn: dict[str, object] = {}
    for code, scheme in ledger.schemes.items():
        key = ledger.nav_keys.get(code, code)
        need = scheme.first_txn_date
        opening = ledger.openings.get(code)
        if opening is not None and opening.as_of is not None and opening.as_of < need:
            need = opening.as_of
        prior = nav_key_first_txn.get(key)
        if prior is None or need < prior:  # type: ignore[operator]
            nav_key_first_txn[key] = need

    await progress(PCT_NAV_FETCH[0], "Checking price history...", phase="fetching_nav")
    with job_span("networth.nav_coverage"):
        gaps = await nav_coverage.find_gaps(db, nav_key_first_txn, today)  # type: ignore[arg-type]

        lo, hi = PCT_NAV_FETCH

        async def on_fetch(done: int, total: int) -> None:
            await progress(
                lo + (done / total) * (hi - lo),
                f"Fetching NAV history... {done}/{total} funds",
            )

        nav_counts = await nav_coverage.close_gaps(gaps, today, on_progress=on_fetch)

    # ── Phase B: replay ──────────────────────────────────────────────────────
    r_lo, r_hi = PCT_REPLAY
    await progress(r_lo, "Calculating daily net worth...", phase="computing")

    per_scheme: dict[str, SchemeSeries] = {}
    positions: list[PositionRow] = []
    codes = sorted(set(ledger.schemes) | set(ledger.openings))
    total_codes = max(1, len(codes))

    with job_span("networth.replay"):
        for idx, code in enumerate(codes):
            scheme = ledger.schemes.get(code)
            nav_key = ledger.nav_keys.get(code, code)
            navs = await load_navs(db, nav_key, today)
            opening = ledger.openings.get(code, Opening())

            series = replay_scheme(
                scheme.txns if scheme else [], navs, today, opening=opening
            )
            per_scheme[code] = series

            positions.append(
                PositionRow(
                    scheme_code=code,
                    units=series.end_units,
                    cost_basis=series.end_cost,
                    opening_units=opening.units,
                    opening_cost=opening.cost,
                    opening_as_of=opening.as_of,
                    ledger_agrees=opening.ledger_agrees,
                    first_txn_date=scheme.first_txn_date if scheme else None,
                    last_txn_date=scheme.last_txn_date if scheme else None,
                    nav_key=nav_key,
                )
            )

            if idx % 5 == 0 or idx == total_codes - 1:
                await progress(
                    r_lo + ((idx + 1) / total_codes) * (r_hi - r_lo),
                    f"Calculating daily net worth... {round((idx + 1) / total_codes * 100)}%",
                )

    # ── Anchor to the authoritative headline ─────────────────────────────────
    await progress(PCT_ANCHOR, "Matching your portfolio total...", phase="anchoring")
    ledger_value_today = sum(
        (s.value_by_day.get(today, ZERO) for s in per_scheme.values()), ZERO
    )
    ledger_invested_today = sum(
        (s.invested_by_day.get(today, ZERO) for s in per_scheme.values()), ZERO
    )
    anchor_value, anchor_invested, anchor_warnings = await _anchor(
        db, user_id, ledger_value_today, ledger_invested_today
    )

    rows, warnings = combine(
        per_scheme,
        today,
        anchor_value=anchor_value,
        anchor_invested=anchor_invested,
    )
    warnings.update(nav_counts)
    warnings.update(anchor_warnings)

    degraded = sum(1 for s in per_scheme.values() if s.degraded)
    stale_value = sum((s.stale_value for s in per_scheme.values()), ZERO)

    # ── Persist ──────────────────────────────────────────────────────────────
    await progress(
        PCT_PERSIST, f"Saving {len(rows)} days of history...", phase="persisting"
    )
    with job_span("networth.persist"):
        await replace_series(
            db,
            user_id,
            rows=rows,
            positions=positions,
            state=SeriesState(
                first_date=rows[0].day if rows else None,
                last_date=rows[-1].day if rows else None,
                row_count=len(rows),
                built_from_cas_upload_id=snapshot_id,
                ledger_complete=ledger.ledger_complete,
                degraded_schemes=degraded,
                stale_priced_value=stale_value,
                anchor_value=anchor_value,
                anchor_invested=anchor_invested,
                last_invested=rows[-1].total_invested if rows else ZERO,
            ),
        )

    return RebuildResult(
        days_written=len(rows),
        schemes=len(per_scheme),
        degraded_schemes=degraded,
        ledger_complete=ledger.ledger_complete,
        warnings=dict(warnings),
    )


async def _anchor(
    db: AsyncSession,
    user_id: uuid.UUID,
    ledger_value: Decimal,
    ledger_invested: Decimal,
) -> tuple[Decimal, Decimal, Counter]:
    """The opening-position adjustment that reconciles the ledger with the headline.

    The series is replayed purely from the transaction ledger. When a CAS covers only a
    recent window — or holdings arrived from a non-ledger source — that ledger is
    *partial*: it understates both value and cost, while the dashboard headline uses the
    true holdings totals. Left alone, the invested line tops out at the partial-ledger
    cost and then jumps to the real total at the last point.

    CAS opening balances (``loader.load_openings``) already close most of that gap
    properly, per scheme and priced at each day's real NAV. This handles whatever is
    left: the residual is held flat across the window so the series stays continuous and
    ends exactly at the headline. For a complete ledger the residual is ~0 and this is a
    no-op — which is what makes it safe to apply unconditionally.

    A *large* residual is not a number to apply quietly, though: it means the ledger and
    the holdings disagree about the portfolio, so it is counted as a warning too.
    """
    warnings: Counter = Counter()
    try:
        from app.domains.portfolio.services.portfolio_service import (
            revalue_primary_portfolio_at_latest_nav,
        )

        portfolio = await revalue_primary_portfolio_at_latest_nav(db, user_id)
    except Exception:  # noqa: BLE001 — never fail the series on a revalue hiccup
        await db.rollback()
        portfolio = None
        warnings["anchor_unavailable"] += 1

    if portfolio is None:
        return ZERO, ZERO, warnings

    auth_value = Decimal(str(portfolio.total_value or 0))
    auth_invested = Decimal(str(portfolio.total_invested or 0))

    anchor_value = ZERO
    anchor_invested = ZERO
    if auth_value > ZERO:
        anchor_value = max(ZERO, auth_value - ledger_value)
    if auth_invested > ZERO:
        anchor_invested = max(ZERO, auth_invested - ledger_invested)

    if auth_value > ZERO and anchor_value > auth_value * ANCHOR_WARN_FRACTION:
        warnings["large_anchor"] += 1
        logger.warning(
            "networth: ledger for user %s is %s short of the holdings total %s "
            "- series anchored, but the two sources disagree",
            user_id,
            anchor_value,
            auth_value,
        )

    return anchor_value, anchor_invested, warnings
