#!/usr/bin/env python3
"""Backfill full NAV history for every ACTIVE scheme already in mf_fund_metadata.

Pairs with ``mfapi_active_funds_sync.py``: that script flips schemes "visible"
by inserting one fresh NAV per scheme; this one fills out the historical NAV
series so chart / returns endpoints have data.

"Active" here = at least one row in ``mf_nav_history`` within the last
``--window-days`` days (default 30) — same definition as the runtime
``_has_recent_nav()`` filter in ``app/services/mf/fund_metadata_service.py``.

For each active scheme this delegates to ``ingest_mfapi`` (incremental mode):
- ``GET /mf/{code}`` → full meta + NAV history.
- Insert NAV rows newer than each scheme's existing ``MAX(nav_date)`` in DB.
- ``ON CONFLICT (scheme_code, nav_date) DO NOTHING`` → idempotent on re-runs.

Run from ``Prozpr_Backend/`` with ``.env`` loaded::

    python scripts/mfapi_active_nav_history_sync.py
    python scripts/mfapi_active_nav_history_sync.py --concurrency 8 --batch-size 100
    python scripts/mfapi_active_nav_history_sync.py --full
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text

from app.database import _get_session_factory
from app.services.mf.mfapi_ingest_service import (
    IngestMode,
    MfapiIngestError,
    ingest_mfapi,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

_DEFAULT_WINDOW_DAYS = 30
_DEFAULT_CONCURRENCY = 8
_DEFAULT_BATCH_SIZE = 100


async def _active_scheme_codes(
    window_days: int, *, missing_history_only: bool, history_threshold: int
) -> list[str]:
    """scheme_codes in mf_fund_metadata that are active (NAV in last N days).

    When ``missing_history_only`` is true, restrict to schemes whose total
    NAV row count is <= ``history_threshold`` — i.e. funds that are missing
    a deep history (typically newly-added schemes that only have today's
    seed row from ``mfapi_active_funds_sync.py``). This avoids re-attempting
    inserts of millions of already-present rows for schemes whose history is
    fully populated.
    """
    cutoff = date.today() - timedelta(days=window_days)
    factory = _get_session_factory()
    async with factory() as db:
        if missing_history_only:
            stmt = text(
                "SELECT m.scheme_code FROM mf_fund_metadata m "
                "WHERE EXISTS ("
                "  SELECT 1 FROM mf_nav_history h "
                "  WHERE h.scheme_code = m.scheme_code AND h.nav_date >= :cutoff"
                ") "
                "AND ("
                "  SELECT COUNT(*) FROM mf_nav_history h2 "
                "  WHERE h2.scheme_code = m.scheme_code"
                ") <= :threshold "
                "ORDER BY m.scheme_code"
            )
            rows = await db.execute(stmt, {"cutoff": cutoff, "threshold": history_threshold})
        else:
            stmt = text(
                "SELECT m.scheme_code FROM mf_fund_metadata m "
                "WHERE EXISTS ("
                "  SELECT 1 FROM mf_nav_history h "
                "  WHERE h.scheme_code = m.scheme_code AND h.nav_date >= :cutoff"
                ") "
                "ORDER BY m.scheme_code"
            )
            rows = await db.execute(stmt, {"cutoff": cutoff})
        return [str(r[0]).strip() for r in rows.all() if str(r[0]).strip()]


def _fmt_duration(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}h{m:02d}m{s:02d}s"
    if m:
        return f"{m}m{s:02d}s"
    return f"{s}s"


async def _run(args: argparse.Namespace) -> int:
    mode = IngestMode.FULL if args.full else IngestMode.INCREMENTAL
    logger.info(
        "Resolving active schemes (NAV in last %d days)%s …",
        args.window_days,
        f" with <={args.history_threshold} existing NAV rows"
        if args.missing_history_only else "",
    )
    codes = await _active_scheme_codes(
        args.window_days,
        missing_history_only=args.missing_history_only,
        history_threshold=args.history_threshold,
    )
    logger.info("Active schemes to process: %d", len(codes))

    if args.limit:
        codes = codes[: args.limit]
        logger.info("--limit: trimmed to %d", len(codes))

    if not codes:
        logger.info("Nothing to do.")
        return 0

    batch = max(1, args.batch_size)
    total_batches = (len(codes) + batch - 1) // batch
    logger.info(
        "Plan: %d batches × %d  mode=%s  concurrency=%d  dry_run=%s",
        total_batches, batch, mode.value, args.concurrency, args.dry_run,
    )

    factory = _get_session_factory()
    totals = {
        "schemes": 0, "metadata_updated": 0, "nav_inserted": 0,
        "nav_candidates": 0, "failed_schemes": 0, "failed_batches": 0,
    }
    t0 = time.monotonic()

    for batch_idx in range(total_batches):
        chunk = codes[batch_idx * batch : (batch_idx + 1) * batch]
        logger.info(
            "── batch %d/%d (%d codes) [%s … %s] ──",
            batch_idx + 1, total_batches, len(chunk), chunk[0], chunk[-1],
        )

        async with factory() as db:
            try:
                r = await ingest_mfapi(
                    db,
                    mode=mode,
                    scheme_codes=chunk,
                    dry_run=args.dry_run,
                    concurrency=args.concurrency,
                    metadata_only=False,
                )
            except MfapiIngestError as exc:
                logger.error("  batch %d FAILED: %s — continuing", batch_idx + 1, exc)
                totals["failed_batches"] += 1
                continue

        totals["schemes"] += r.schemes_seen
        totals["metadata_updated"] += r.schemes_updated + r.schemes_inserted
        totals["nav_inserted"] += r.nav_rows_inserted
        totals["nav_candidates"] += r.nav_rows_candidate
        totals["failed_schemes"] += len(r.failed_codes)

        elapsed = time.monotonic() - t0
        done = (batch_idx + 1) * batch
        rate = done / elapsed if elapsed > 0 else 0
        eta = (len(codes) - done) / rate if rate > 0 else 0
        logger.info(
            "  done: nav_inserted=%d candidates=%d failed=%d  "
            "running nav=%d  elapsed=%s  eta=%s",
            r.nav_rows_inserted, r.nav_rows_candidate, len(r.failed_codes),
            totals["nav_inserted"], _fmt_duration(elapsed), _fmt_duration(eta),
        )

    logger.info(
        "═══ DONE ═══  mode=%s schemes=%d metadata_updated=%d "
        "nav_inserted=%d nav_candidates=%d failed_schemes=%d "
        "failed_batches=%d  elapsed=%s  dry_run=%s",
        mode.value, totals["schemes"], totals["metadata_updated"],
        totals["nav_inserted"], totals["nav_candidates"],
        totals["failed_schemes"], totals["failed_batches"],
        _fmt_duration(time.monotonic() - t0), args.dry_run,
    )
    return 0


def main() -> None:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    grp = p.add_mutually_exclusive_group()
    grp.add_argument(
        "--incremental", action="store_true",
        help="Only insert NAVs newer than per-scheme MAX(nav_date) (default).",
    )
    grp.add_argument(
        "--full", action="store_true",
        help="Insert all NAV points returned by mfapi (use for first-time deep backfill).",
    )
    p.add_argument(
        "--window-days", type=int, default=_DEFAULT_WINDOW_DAYS,
        help=f"Active = NAV in last N days (default {_DEFAULT_WINDOW_DAYS}).",
    )
    p.add_argument(
        "--concurrency", type=int, default=_DEFAULT_CONCURRENCY,
        help=f"Parallel mfapi /mf/{{code}} requests (default {_DEFAULT_CONCURRENCY}). "
             "Each call returns full NAV history; keep modest to avoid timeouts.",
    )
    p.add_argument(
        "--batch-size", type=int, default=_DEFAULT_BATCH_SIZE,
        help=f"Schemes per batch; each batch commits independently "
             f"(default {_DEFAULT_BATCH_SIZE}).",
    )
    p.add_argument(
        "--missing-history-only", action="store_true", default=True,
        help="Only process schemes whose existing NAV row count is "
             "<= --history-threshold. Default ON (recommended after "
             "mfapi_active_funds_sync.py): cuts work to schemes that need "
             "real backfill. Pass --all-active to disable.",
    )
    p.add_argument(
        "--all-active", dest="missing_history_only", action="store_false",
        help="Process every active scheme regardless of existing history depth.",
    )
    p.add_argument(
        "--history-threshold", type=int, default=5,
        help="With --missing-history-only (default), only schemes with at most "
             "this many NAV rows in DB are processed (default 5 — schemes seeded "
             "by mfapi_active_funds_sync.py have exactly 1).",
    )
    p.add_argument(
        "--limit", type=int, default=None,
        help="Process only the first N active codes (smoke test).",
    )
    p.add_argument(
        "--dry-run", action="store_true",
        help="Fetch + parse only; no DB writes.",
    )
    args = p.parse_args()
    if not args.full and not args.incremental:
        args.incremental = True
    raise SystemExit(asyncio.run(_run(args)))


if __name__ == "__main__":
    main()
