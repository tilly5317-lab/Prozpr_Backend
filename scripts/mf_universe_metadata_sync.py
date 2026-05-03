#!/usr/bin/env python3
"""Populate ``mf_fund_metadata`` from one GET to ``https://api.mfapi.in/mf``.

Uses the list payload only (``schemeCode``, ``schemeName``, optional ``isinGrowth``,
``isinDivReinvestment``). No per-scheme ``/mf/{code}`` calls — suitable for loading
the full ~37k scheme catalogue quickly.

For rows that already exist, only fields sourced from this list are updated
(name, ISINs, derived plan/option, ``is_active``). Existing ``amc_name`` /
``category`` / ``sub_category`` from a richer ingest are left unchanged.

Run from ``Prozpr_Backend/`` with ``.env`` loaded.

Examples::

  python scripts/mf_universe_metadata_sync.py
  python scripts/mf_universe_metadata_sync.py --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import _get_session_factory
from app.models.mf import MfFundMetadata
from app.services.mf.mfapi_fetcher import (
    MFAPI_TIMEOUT,
    UniverseRow,
    _derive_option_type,
    _derive_plan_type,
    fetch_universe,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
)
logger = logging.getLogger(__name__)

_CHUNK = 500


def _truncate(value: str | None, length: int) -> str | None:
    if value is None:
        return None
    s = value.strip()
    if not s:
        return None
    return s[:length] if len(s) > length else s


def _row_from_universe(u: UniverseRow) -> dict:
    name = _truncate(u.scheme_name, 200) or "Unknown"
    return {
        "scheme_code": _truncate(u.scheme_code, 20) or u.scheme_code,
        "isin": u.isin_growth,
        "isin_div_reinvest": u.isin_div_reinvest,
        "scheme_name": name,
        "amc_name": "Unknown",
        "category": "Unknown",
        "sub_category": None,
        "plan_type": _derive_plan_type(u.scheme_name),
        "option_type": _derive_option_type(u.scheme_name),
        "is_active": True,
    }


async def _upsert_chunk(db: AsyncSession, rows: list[dict]) -> None:
    stmt = pg_insert(MfFundMetadata).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=["scheme_code"],
        set_={
            "scheme_name": stmt.excluded.scheme_name,
            "isin": stmt.excluded.isin,
            "isin_div_reinvest": stmt.excluded.isin_div_reinvest,
            "plan_type": stmt.excluded.plan_type,
            "option_type": stmt.excluded.option_type,
            "is_active": stmt.excluded.is_active,
        },
    )
    await db.execute(stmt)


async def _run(*, dry_run: bool) -> int:
    logger.info("Fetching https://api.mfapi.in/mf …")
    async with httpx.AsyncClient(timeout=MFAPI_TIMEOUT, follow_redirects=True) as client:
        universe = await fetch_universe(client)

    logger.info("Parsed %d schemes from /mf", len(universe))
    rows = [_row_from_universe(u) for u in universe]

    if dry_run:
        with_isin = sum(1 for u in universe if u.isin_growth or u.isin_div_reinvest)
        logger.info(
            "dry-run: would upsert %d rows (%d with at least one ISIN)",
            len(rows),
            with_isin,
        )
        return 0

    logger.info(
        "Writing to mf_fund_metadata in chunks of %d (this can take a few minutes over RDS) …",
        _CHUNK,
    )
    factory = _get_session_factory()
    async with factory() as db:
        for start in range(0, len(rows), _CHUNK):
            chunk = rows[start : start + _CHUNK]
            await _upsert_chunk(db, chunk)
            logger.info(
                "Upserted chunk %d–%d / %d",
                start + 1,
                min(start + len(chunk), len(rows)),
                len(rows),
            )
        await db.commit()

    logger.info("Done. mf_fund_metadata upserted for %d scheme_code(s).", len(rows))
    return 0


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dry-run", action="store_true", help="Parse + count only; no DB writes.")
    args = p.parse_args()
    raise SystemExit(asyncio.run(_run(dry_run=args.dry_run)))


if __name__ == "__main__":
    main()
