#!/usr/bin/env python3
"""Sync every ACTIVE mutual fund (latest NAV in last N days) from api.mfapi.in
into ``mf_fund_metadata`` (and one fresh NAV row into ``mf_nav_history``).

Why a dedicated script: the existing ``mf_universe_metadata_sync.py`` upserts
the *whole* universe (~50k schemes, including dormant ones) but never inserts
NAV rows — so the runtime ``_has_recent_nav()`` filter in
``app/services/mf/fund_metadata_service.py`` hides them all. This script
keeps the metadata table tight (only schemes mfapi.in still publishes NAVs
for) and seeds ``mf_nav_history`` with each scheme's latest NAV so they
immediately show up in list/search endpoints.

Per scheme:
1. ``GET /mf/{code}/latest`` → meta + 1 NAV point.
2. Keep only if NAV date is within ``--window-days`` (default 30).
3. Upsert ``mf_fund_metadata`` (idempotent on scheme_code).
4. Bulk-insert the latest NAV into ``mf_nav_history`` with
   ``ON CONFLICT (scheme_code, nav_date) DO NOTHING``.

Run from ``Prozpr_Backend/`` with ``.env`` loaded::

    python scripts/mfapi_active_funds_sync.py
    python scripts/mfapi_active_funds_sync.py --concurrency 20 --batch-size 500
    python scripts/mfapi_active_funds_sync.py --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import _get_session_factory
from app.models.mf import MfFundMetadata
from app.services.mf.mfapi_fetcher import (
    MFAPI_BASE,
    MFAPI_TIMEOUT,
    _coerce_isin,
    _derive_option_type,
    _derive_plan_type,
    _request_json,
    fetch_universe,
)
from app.services.mf.mfapi_ingest_service import _split_category, _truncate
from app.services.mf.nav_history_service import bulk_insert_nav_rows

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

_DEFAULT_WINDOW_DAYS = 30
_DEFAULT_CONCURRENCY = 15
_DEFAULT_BATCH_SIZE = 500


@dataclass(slots=True)
class ActiveScheme:
    scheme_code: str
    scheme_name: str
    fund_house: str
    scheme_type: str
    scheme_category: str
    isin_growth: Optional[str]
    isin_div_reinvest: Optional[str]
    nav_date: date
    nav: Decimal


async def _fetch_latest(
    client: httpx.AsyncClient, scheme_code: str
) -> Optional[ActiveScheme]:
    """``GET /mf/{code}/latest``. None on any miss / parse failure."""
    try:
        payload = await _request_json(client, f"{MFAPI_BASE}/mf/{scheme_code}/latest")
    except Exception:
        return None

    if not isinstance(payload, dict):
        return None
    if str(payload.get("status") or "").upper() != "SUCCESS":
        return None

    meta = payload.get("meta") or {}
    data = payload.get("data") or []
    if not isinstance(meta, dict) or not isinstance(data, list) or not data:
        return None

    pt = data[0]
    if not isinstance(pt, dict):
        return None
    try:
        nav_date_v = datetime.strptime(str(pt.get("date")).strip(), "%d-%m-%Y").date()
        nav_v = Decimal(str(pt.get("nav")).strip())
    except (ValueError, InvalidOperation, TypeError):
        return None

    code = str(meta.get("scheme_code") or scheme_code).strip()
    name = str(meta.get("scheme_name") or "").strip()
    if not code or not name:
        return None

    return ActiveScheme(
        scheme_code=code,
        scheme_name=name,
        fund_house=str(meta.get("fund_house") or "").strip() or "Unknown",
        scheme_type=str(meta.get("scheme_type") or "").strip(),
        scheme_category=str(meta.get("scheme_category") or "").strip(),
        isin_growth=_coerce_isin(meta.get("isin_growth")),
        isin_div_reinvest=_coerce_isin(meta.get("isin_div_reinvestment")),
        nav_date=nav_date_v,
        nav=nav_v,
    )


async def _fetch_many_latest(
    client: httpx.AsyncClient,
    scheme_codes: list[str],
    *,
    concurrency: int,
) -> tuple[list[ActiveScheme], int]:
    sem = asyncio.Semaphore(concurrency)
    results: list[ActiveScheme] = []
    fails = 0

    async def _one(code: str) -> None:
        nonlocal fails
        async with sem:
            r = await _fetch_latest(client, code)
            if r is None:
                fails += 1
            else:
                results.append(r)

    await asyncio.gather(*(_one(c) for c in scheme_codes))
    return results, fails


def _meta_row(s: ActiveScheme) -> dict:
    category, sub_category = _split_category(s.scheme_category)
    return {
        "scheme_code": _truncate(s.scheme_code, 20) or s.scheme_code,
        "isin": s.isin_growth,
        "isin_div_reinvest": s.isin_div_reinvest,
        "scheme_name": _truncate(s.scheme_name, 200) or s.scheme_name,
        "amc_name": _truncate(s.fund_house, 100) or s.fund_house,
        "category": _truncate(category, 50) or "Unknown",
        "sub_category": _truncate(sub_category, 100),
        "plan_type": _derive_plan_type(s.scheme_name),
        "option_type": _derive_option_type(s.scheme_name),
        "is_active": True,
    }


def _nav_row(s: ActiveScheme) -> dict:
    mf_type = " | ".join(p for p in (s.scheme_type, s.scheme_category) if p) or "Unknown"
    return {
        "scheme_code": s.scheme_code,
        "isin": s.isin_growth,
        "scheme_name": _truncate(s.scheme_name, 200) or s.scheme_name,
        "mf_type": _truncate(mf_type, 200) or "Unknown",
        "nav": s.nav,
        "nav_date": s.nav_date,
    }


def _dedup_isin_in_batch(active: list[ActiveScheme]) -> int:
    """Resolve duplicate ISINs in a single batch — DB has a partial unique index
    on ``mf_fund_metadata.isin WHERE isin IS NOT NULL``. Returns collisions."""
    seen: dict[str, ActiveScheme] = {}
    collisions = 0
    for s in active:
        if not s.isin_growth:
            continue
        prior = seen.get(s.isin_growth)
        if prior is None:
            seen[s.isin_growth] = s
        else:
            collisions += 1
            logger.warning(
                "isin_collision: %s on %s and %s — NULL on %s",
                s.isin_growth, prior.scheme_code, s.scheme_code, s.scheme_code,
            )
            s.isin_growth = None
    return collisions


async def _persist_active(
    db: AsyncSession, active: list[ActiveScheme]
) -> tuple[int, int]:
    if not active:
        return 0, 0

    meta_rows = [_meta_row(s) for s in active]
    stmt = pg_insert(MfFundMetadata).values(meta_rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=["scheme_code"],
        set_={
            "isin": stmt.excluded.isin,
            "isin_div_reinvest": stmt.excluded.isin_div_reinvest,
            "scheme_name": stmt.excluded.scheme_name,
            "amc_name": stmt.excluded.amc_name,
            "category": stmt.excluded.category,
            "sub_category": stmt.excluded.sub_category,
            "plan_type": stmt.excluded.plan_type,
            "option_type": stmt.excluded.option_type,
            "is_active": stmt.excluded.is_active,
        },
    )
    await db.execute(stmt)

    nav_inserted = await bulk_insert_nav_rows(db, [_nav_row(s) for s in active])
    return len(meta_rows), nav_inserted


async def _run(args: argparse.Namespace) -> int:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=args.window_days)).date()
    logger.info(
        "Active cutoff: latest NAV date >= %s  (window=%d days)",
        cutoff, args.window_days,
    )

    async with httpx.AsyncClient(timeout=MFAPI_TIMEOUT, follow_redirects=True) as client:
        logger.info("Fetching universe from %s/mf …", MFAPI_BASE)
        universe = await fetch_universe(client)
        all_codes = [u.scheme_code for u in universe]
        logger.info("Universe size: %d schemes", len(all_codes))

        if args.limit:
            all_codes = all_codes[: args.limit]
            logger.info("--limit: trimmed to first %d codes", len(all_codes))

        batch = max(1, args.batch_size)
        total_batches = (len(all_codes) + batch - 1) // batch
        factory = _get_session_factory()

        totals = {
            "checked": 0, "active": 0, "stale": 0, "failed": 0,
            "metadata_upserted": 0, "nav_inserted": 0, "isin_collisions": 0,
        }
        t0 = time.monotonic()

        for batch_idx in range(total_batches):
            chunk_codes = all_codes[batch_idx * batch : (batch_idx + 1) * batch]
            logger.info(
                "── batch %d/%d  (%d codes) ──",
                batch_idx + 1, total_batches, len(chunk_codes),
            )

            latest, failed = await _fetch_many_latest(
                client, chunk_codes, concurrency=args.concurrency
            )
            active = [s for s in latest if s.nav_date >= cutoff]
            stale = len(latest) - len(active)

            collisions = _dedup_isin_in_batch(active)

            totals["checked"] += len(chunk_codes)
            totals["failed"] += failed
            totals["active"] += len(active)
            totals["stale"] += stale
            totals["isin_collisions"] += collisions

            if active and not args.dry_run:
                async with factory() as db:
                    try:
                        meta_count, nav_count = await _persist_active(db, active)
                        await db.commit()
                    except Exception:
                        await db.rollback()
                        logger.exception(
                            "  batch %d DB write failed — continuing", batch_idx + 1
                        )
                        continue
                totals["metadata_upserted"] += meta_count
                totals["nav_inserted"] += nav_count

            elapsed = time.monotonic() - t0
            done = (batch_idx + 1) * batch
            rate = done / elapsed if elapsed > 0 else 0
            eta = (len(all_codes) - done) / rate if rate > 0 else 0
            logger.info(
                "  batch: active=%d stale=%d failed=%d collisions=%d  "
                "running active=%d  elapsed=%.0fs  eta=%.0fs",
                len(active), stale, failed, collisions,
                totals["active"], elapsed, eta,
            )

    logger.info(
        "═══ DONE ═══  checked=%d  active=%d  stale=%d  failed=%d  "
        "metadata_upserted=%d  nav_inserted=%d  isin_collisions=%d  elapsed=%.0fs  dry_run=%s",
        totals["checked"], totals["active"], totals["stale"], totals["failed"],
        totals["metadata_upserted"], totals["nav_inserted"],
        totals["isin_collisions"],
        time.monotonic() - t0, args.dry_run,
    )
    return 0


def main() -> None:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--window-days", type=int, default=_DEFAULT_WINDOW_DAYS,
        help=f"Active = latest NAV within N days (default {_DEFAULT_WINDOW_DAYS}).",
    )
    p.add_argument(
        "--concurrency", type=int, default=_DEFAULT_CONCURRENCY,
        help=f"Parallel mfapi.in requests (default {_DEFAULT_CONCURRENCY}).",
    )
    p.add_argument(
        "--batch-size", type=int, default=_DEFAULT_BATCH_SIZE,
        help=f"Schemes per batch; each batch commits independently "
             f"(default {_DEFAULT_BATCH_SIZE}).",
    )
    p.add_argument(
        "--limit", type=int, default=None,
        help="Process only the first N codes from the universe (smoke test).",
    )
    p.add_argument(
        "--dry-run", action="store_true",
        help="Fetch + filter only; no DB writes.",
    )
    args = p.parse_args()
    raise SystemExit(asyncio.run(_run(args)))


if __name__ == "__main__":
    main()
