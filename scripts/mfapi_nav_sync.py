#!/usr/bin/env python3
"""Pull NAV history from mfapi.in into Postgres in resumable batches.

Fetches the scheme universe (or a subset), skips schemes that already have NAV
data in the DB, then ingests the remainder in batches — each batch commits
independently so progress is durable and the run is naturally resumable.

Examples (from ``Prozpr_Backend/`` with ``.env`` loaded):

  # Full historical load for every scheme (resumable — re-run picks up where it left off)
  python scripts/mfapi_nav_sync.py --full --concurrency 5

  # Incremental NAV refresh for all schemes (only new dates since last run)
  python scripts/mfapi_nav_sync.py --incremental --no-resume --concurrency 5

  # Only schemes already in mf_fund_metadata
  python scripts/mfapi_nav_sync.py --full --source metadata --concurrency 3

  # Explicit scheme codes
  python scripts/mfapi_nav_sync.py --full --schemes 120716,119551

  # Re-do everything from scratch (ignore what's already in DB)
  python scripts/mfapi_nav_sync.py --full --no-resume --concurrency 5
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
from sqlalchemy import select, text

from app.database import _get_session_factory
from app.models.mf import MfFundMetadata
from app.services.mf.mfapi_fetcher import MFAPI_TIMEOUT, fetch_universe
from app.services.mf.mfapi_ingest_service import IngestMode, MfapiIngestError, ingest_mfapi

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
)
logger = logging.getLogger(__name__)

_DEFAULT_BATCH_SIZE = 50


async def _scheme_codes_from_metadata() -> list[str]:
    factory = _get_session_factory()
    async with factory() as db:
        r = await db.execute(select(MfFundMetadata.scheme_code))
        return [str(row[0]).strip() for row in r.all() if str(row[0]).strip()]


async def _scheme_codes_with_nav_data() -> set[str]:
    """Scheme codes that already have at least one committed row in mf_nav_history."""
    factory = _get_session_factory()
    async with factory() as db:
        r = await db.execute(text("SELECT DISTINCT scheme_code FROM mf_nav_history"))
        return {str(row[0]).strip() for row in r.all()}


async def _fetch_universe_codes() -> list[str]:
    async with httpx.AsyncClient(timeout=MFAPI_TIMEOUT, follow_redirects=True) as client:
        universe = await fetch_universe(client)
    return [row.scheme_code for row in universe]


def _fmt_duration(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}h{m:02d}m{s:02d}s"
    if m:
        return f"{m}m{s:02d}s"
    return f"{s}s"


async def _run(args: argparse.Namespace) -> int:
    mode = IngestMode.INCREMENTAL if args.incremental else IngestMode.FULL
    batch_size: int = args.batch_size

    # ── 1. Build the full list of scheme codes ─────────────────────
    if args.schemes:
        all_codes = [c.strip() for c in args.schemes.split(",") if c.strip()]
        logger.info("Explicit scheme codes: %d", len(all_codes))
    elif args.source == "metadata":
        all_codes = await _scheme_codes_from_metadata()
        if not all_codes:
            logger.error(
                "No rows in mf_fund_metadata; run a full-universe ingest first or use --schemes."
            )
            return 1
        logger.info("Loaded %d scheme codes from mf_fund_metadata", len(all_codes))
    else:
        logger.info("Fetching scheme universe from mfapi.in /mf …")
        all_codes = await _fetch_universe_codes()
        logger.info("mfapi.in universe: %d scheme codes", len(all_codes))

    # ── 2. Resume: skip codes that already have data ─────────────
    remaining = all_codes
    if args.resume and not args.schemes:
        if args.metadata_only:
            already_done = set(await _scheme_codes_from_metadata())
            label = "metadata"
        else:
            already_done = await _scheme_codes_with_nav_data()
            label = "NAV rows"
        remaining = [c for c in all_codes if c not in already_done]
        skipped = len(all_codes) - len(remaining)
        if skipped:
            logger.info(
                "Resume: %d schemes already have %s — skipped.  %d remaining.",
                skipped,
                label,
                len(remaining),
            )

    if not remaining:
        logger.info("All %d schemes already processed. Nothing to do.", len(all_codes))
        return 0

    logger.info(
        "Will process %d schemes in batches of %d  (mode=%s, concurrency=%d, dry_run=%s)",
        len(remaining),
        batch_size,
        mode.value,
        args.concurrency,
        args.dry_run,
    )

    # ── 3. Process in batches ──────────────────────────────────────
    total_batches = (len(remaining) + batch_size - 1) // batch_size
    agg_nav_inserted = 0
    agg_nav_candidates = 0
    agg_schemes = 0
    agg_failed_schemes = 0
    agg_failed_batches = 0
    t0 = time.monotonic()

    for batch_start in range(0, len(remaining), batch_size):
        batch = remaining[batch_start : batch_start + batch_size]
        batch_num = batch_start // batch_size + 1

        logger.info(
            "━━ batch %d/%d ━━  %d schemes [%s … %s]",
            batch_num,
            total_batches,
            len(batch),
            batch[0],
            batch[-1],
        )

        factory = _get_session_factory()
        async with factory() as db:
            try:
                result = await ingest_mfapi(
                    db,
                    mode=mode,
                    scheme_codes=batch,
                    dry_run=args.dry_run,
                    concurrency=args.concurrency,
                    metadata_only=args.metadata_only,
                )
            except MfapiIngestError as exc:
                logger.error("  batch %d FAILED: %s", batch_num, exc)
                agg_failed_batches += 1
                continue

        agg_nav_inserted += result.nav_rows_inserted
        agg_nav_candidates += result.nav_rows_candidate
        agg_schemes += result.schemes_seen
        agg_failed_schemes += len(result.failed_codes)

        elapsed = time.monotonic() - t0
        done_so_far = batch_start + len(batch)
        rate = done_so_far / elapsed if elapsed > 0 else 0
        eta_s = (len(remaining) - done_so_far) / rate if rate > 0 else 0

        logger.info(
            "  done: nav_inserted=%d  candidates=%d  failed=%d",
            result.nav_rows_inserted,
            result.nav_rows_candidate,
            len(result.failed_codes),
        )
        logger.info(
            "  progress: %d/%d schemes (%d%%)  total_nav=%d  elapsed=%s  ETA≈%s",
            done_so_far,
            len(remaining),
            min(100, done_so_far * 100 // len(remaining)),
            agg_nav_inserted,
            _fmt_duration(elapsed),
            _fmt_duration(eta_s),
        )

    # ── 4. Summary ─────────────────────────────────────────────────
    elapsed_total = time.monotonic() - t0
    logger.info(
        "═══ COMPLETE ═══  mode=%s  schemes=%d  nav_inserted=%d  nav_candidates=%d  "
        "failed_schemes=%d  failed_batches=%d  elapsed=%s  dry_run=%s",
        mode.value,
        agg_schemes,
        agg_nav_inserted,
        agg_nav_candidates,
        agg_failed_schemes,
        agg_failed_batches,
        _fmt_duration(elapsed_total),
        args.dry_run,
    )
    return 0


def main() -> None:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    grp = p.add_mutually_exclusive_group()
    grp.add_argument(
        "--incremental",
        action="store_true",
        help="Only insert NAV rows newer than each scheme's MAX(nav_date) in DB (default).",
    )
    grp.add_argument(
        "--full",
        action="store_true",
        help="Insert all NAV points returned by mfapi (use for first-time backfill).",
    )
    p.add_argument(
        "--source",
        choices=("universe", "metadata"),
        default="universe",
        help="Where to get scheme codes: mfapi.in /mf (all ~50K funds) or local mf_fund_metadata.",
    )
    p.add_argument(
        "--schemes",
        type=str,
        default=None,
        help="Comma-separated scheme codes; overrides --source.",
    )
    p.add_argument(
        "--concurrency",
        type=int,
        default=1,
        help="Parallel mfapi.in HTTP requests (default 1; use 5-10 for speed).",
    )
    p.add_argument(
        "--batch-size",
        type=int,
        default=_DEFAULT_BATCH_SIZE,
        help=f"Schemes per batch; each batch commits independently (default {_DEFAULT_BATCH_SIZE}). "
        "Lower = faster visible progress; higher = fewer DB round-trips.",
    )
    p.add_argument(
        "--no-resume",
        action="store_true",
        help="Don't skip schemes already in mf_nav_history (re-process everything).",
    )
    p.add_argument(
        "--metadata-only",
        action="store_true",
        help="Only upsert mf_fund_metadata (scheme info); skip NAV history inserts entirely. "
        "Much faster — use to populate the fund catalogue first.",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse and count only; no DB writes.",
    )
    args = p.parse_args()

    if not args.incremental and not args.full:
        args.incremental = True
    args.resume = not args.no_resume

    raise SystemExit(asyncio.run(_run(args)))


if __name__ == "__main__":
    main()
