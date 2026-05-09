#!/usr/bin/env python3
"""Ingest the entire mfapi.in universe in memory-safe chunks.

`ingest_mfapi` accumulates all scheme details + NAV rows in memory before
flushing. The full ~37k-scheme universe with full NAV history is multi-GB,
which OOMs on a dev box. This wrapper fetches the universe once, splits it
into batches, and calls `ingest_mfapi` per batch — committing each batch so
re-runs resume cleanly.

Usage:
    python scripts/mfapi_full_universe_ingest.py
    python scripts/mfapi_full_universe_ingest.py --batch-size 250 --concurrency 16
    python scripts/mfapi_full_universe_ingest.py --resume  # skip codes already in mf_fund_metadata
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
from sqlalchemy import select

from app.database import _get_session_factory
from app.models.mf import MfFundMetadata
from app.services.mf.mfapi_fetcher import MFAPI_TIMEOUT, fetch_universe
from app.services.mf.mfapi_ingest_service import IngestMode, MfapiIngestError, ingest_mfapi

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


async def _existing_codes() -> set[str]:
    factory = _get_session_factory()
    async with factory() as db:
        rows = await db.execute(select(MfFundMetadata.scheme_code))
        return {str(r[0]).strip() for r in rows.all() if str(r[0]).strip()}


async def _run(args: argparse.Namespace) -> int:
    async with httpx.AsyncClient(timeout=MFAPI_TIMEOUT, follow_redirects=True) as client:
        universe = await fetch_universe(client)
    all_codes = [u.scheme_code for u in universe]
    logger.info("mfapi.in universe size: %d", len(all_codes))

    if args.resume:
        existing = await _existing_codes()
        before = len(all_codes)
        all_codes = [c for c in all_codes if c not in existing]
        logger.info("--resume: skipping %d already-ingested codes; %d remaining",
                    before - len(all_codes), len(all_codes))

    if not all_codes:
        logger.info("Nothing to do.")
        return 0

    batch = max(1, args.batch_size)
    total_batches = (len(all_codes) + batch - 1) // batch
    factory = _get_session_factory()

    started = time.time()
    totals = {
        "seen": 0, "inserted": 0, "updated": 0, "nav_candidates": 0,
        "nav_inserted": 0, "failed": 0, "parse_errors": 0,
    }

    for idx in range(total_batches):
        chunk = all_codes[idx * batch : (idx + 1) * batch]
        logger.info("=== batch %d/%d (size=%d) ===", idx + 1, total_batches, len(chunk))
        async with factory() as db:
            try:
                r = await ingest_mfapi(
                    db,
                    mode=IngestMode.INCREMENTAL,
                    scheme_codes=chunk,
                    dry_run=args.dry_run,
                    concurrency=args.concurrency,
                    metadata_only=args.metadata_only,
                )
            except MfapiIngestError as exc:
                logger.error("batch %d failed: %s — continuing", idx + 1, exc)
                continue
        totals["seen"] += r.schemes_seen
        totals["inserted"] += r.schemes_inserted
        totals["updated"] += r.schemes_updated
        totals["nav_candidates"] += r.nav_rows_candidate
        totals["nav_inserted"] += r.nav_rows_inserted
        totals["failed"] += len(r.failed_codes)
        totals["parse_errors"] += r.parse_errors
        elapsed = time.time() - started
        done = (idx + 1) * batch
        rate = done / elapsed if elapsed > 0 else 0
        eta = (len(all_codes) - done) / rate if rate > 0 else float("inf")
        logger.info(
            "running totals: inserted=%d updated=%d nav_inserted=%d failed=%d  "
            "elapsed=%.0fs  eta=%.0fs",
            totals["inserted"], totals["updated"], totals["nav_inserted"],
            totals["failed"], elapsed, eta,
        )

    logger.info("=== done — totals: %s ===", totals)
    return 0


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--batch-size", type=int, default=250)
    p.add_argument("--concurrency", type=int, default=12)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--resume", action="store_true",
                   help="Skip scheme_codes already present in mf_fund_metadata.")
    p.add_argument("--metadata-only", action="store_true",
                   help="Skip NAV history inserts; only upsert mf_fund_metadata. Much faster for first-time seeding.")
    args = p.parse_args()
    raise SystemExit(asyncio.run(_run(args)))


if __name__ == "__main__":
    main()
