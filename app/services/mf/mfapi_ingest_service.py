"""mfapi.in master + NAV ingestion pipeline.

A single run:
1. Fetches the universe (``GET /mf``) — list of every scheme code.
2. Fetches per-scheme detail (``GET /mf/{code}``) with bounded concurrency.
3. Upserts ``mf_fund_metadata`` keyed by ``scheme_code`` (existing UUIDs preserved
   by ``on_conflict_do_update``); populates the new ``isin`` / ``isin_div_reinvest``
   columns from the AMFI-published ISINs returned by mfapi.in.
4. Bulk-inserts NAV rows into ``mf_nav_history`` with
   ``ON CONFLICT (scheme_code, nav_date) DO NOTHING`` so re-runs are idempotent.

In incremental mode a per-scheme high-water mark (``MAX(nav_date)``) trims NAV
inserts to only newer points — used by the daily 00:00 IST scheduler.

Backfill helper fills NULL ISINs on already-ingested AA / NAV rows by joining on
``scheme_code`` once the canonical metadata table has them.
"""

from __future__ import annotations

import enum
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Iterable, Optional

import httpx
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.mf import MfFundMetadata
from app.services.mf.mfapi_fetcher import (
    MFAPI_CONCURRENCY,
    MFAPI_TIMEOUT,
    MfapiFetchError,
    SchemeDetail,
    fetch_many_scheme_details,
    fetch_universe,
)
from app.services.mf.nav_history_service import bulk_insert_nav_rows

logger = logging.getLogger(__name__)


class IngestMode(str, enum.Enum):
    FULL = "full"
    INCREMENTAL = "incremental"


class MfapiIngestError(RuntimeError):
    """Raised when the mfapi ingest pipeline cannot complete."""


@dataclass(slots=True)
class MfapiIngestResult:
    mode: str
    started_at: datetime
    finished_at: datetime
    schemes_seen: int = 0
    schemes_inserted: int = 0
    schemes_updated: int = 0
    nav_rows_inserted: int = 0
    #: Rows considered for insert after incremental filtering (before ON CONFLICT).
    nav_rows_candidate: int = 0
    nav_rows_skipped_duplicate: int = 0
    isin_collisions: int = 0
    parse_errors: int = 0
    failed_codes: list[str] = field(default_factory=list)
    dry_run: bool = False


@dataclass(slots=True)
class BackfillIsinResult:
    aa_summaries_isin_filled: int = 0
    aa_transactions_isin_filled: int = 0
    nav_history_isin_filled: int = 0
    aa_summaries_scheme_filled: int = 0
    aa_transactions_scheme_filled: int = 0


_METADATA_CHUNK = 500
_NAV_CHUNK = 1000


def _truncate(value: Optional[str], length: int) -> Optional[str]:
    """Trim string fields to DB-safe lengths while preserving None."""
    if value is None:
        return None
    return value[:length] if len(value) > length else value


def _split_category(scheme_category: str) -> tuple[str, Optional[str]]:
    """Split category into (category, sub_category) using ' - ' when present."""
    if not scheme_category:
        return "Unknown", None
    if " - " in scheme_category:
        head, _, tail = scheme_category.partition(" - ")
        return head.strip() or "Unknown", tail.strip() or None
    return scheme_category.strip() or "Unknown", None


async def _resolve_universe_codes(
    client: httpx.AsyncClient,
    scheme_codes: Optional[list[str]],
) -> list[str]:
    """Use explicit scheme codes if provided, otherwise fetch all universe codes."""
    if scheme_codes:
        return [str(c).strip() for c in scheme_codes if str(c).strip()]
    universe = await fetch_universe(client)
    return [row.scheme_code for row in universe]


async def _universe_codes_after_last_in_db(
    client: httpx.AsyncClient,
    db: AsyncSession,
) -> list[str]:
    """Return mfapi `/mf` scheme codes starting after the last index already in ``mf_fund_metadata``.

    Uses the API's universe order only. New schemes inserted mid-list by mfapi would not be
    covered until a run without this filter; use a full sweep periodically if needed.
    """
    ordered = await _resolve_universe_codes(client, None)
    if not ordered:
        return []
    pos = {c: i for i, c in enumerate(ordered)}
    rows = await db.execute(select(MfFundMetadata.scheme_code))
    db_codes = {str(row[0]).strip() for row in rows.all() if str(row[0]).strip()}
    best = -1
    for c in db_codes:
        if c in pos:
            best = max(best, pos[c])
    tail = ordered[best + 1 :]
    skipped = best + 1
    logger.info(
        "resume_from_db: mfapi universe order — skipped %d scheme(s), %d remaining",
        skipped,
        len(tail),
    )
    return tail


async def _high_water_marks(
    db: AsyncSession, scheme_codes: list[str]
) -> dict[str, date]:
    """Return per-scheme latest NAV date to support incremental inserts."""
    if not scheme_codes:
        return {}
    rows = await db.execute(
        text(
            "SELECT scheme_code, MAX(nav_date) AS max_date FROM mf_nav_history "
            "WHERE scheme_code = ANY(:codes) GROUP BY scheme_code"
        ),
        {"codes": scheme_codes},
    )
    return {str(code): d for code, d in rows.all() if d is not None}


async def _existing_scheme_codes(
    db: AsyncSession, scheme_codes: Iterable[str]
) -> set[str]:
    """Fetch the subset of provided scheme codes that already exist in metadata."""
    codes = list(scheme_codes)
    if not codes:
        return set()
    result = await db.execute(
        select(MfFundMetadata.scheme_code).where(MfFundMetadata.scheme_code.in_(codes))
    )
    return {str(row[0]) for row in result.all()}


def _dedup_isin(
    details: list[SchemeDetail], existing_codes: set[str]
) -> int:
    """Resolve duplicate ISINs across scheme codes in-place. Returns collision count."""
    by_isin: dict[str, list[SchemeDetail]] = defaultdict(list)
    for d in details:
        if d.isin_growth:
            by_isin[d.isin_growth].append(d)
    collisions = 0
    for isin, group in by_isin.items():
        if len(group) <= 1:
            continue
        collisions += len(group) - 1
        # Prefer existing-DB scheme_code; otherwise the first encountered.
        keeper = next((g for g in group if g.scheme_code in existing_codes), group[0])
        for d in group:
            if d is keeper:
                continue
            logger.warning(
                "isin_collision: ISIN %s appears under scheme codes %s and %s; "
                "keeping on %s, NULL on %s",
                isin,
                keeper.scheme_code,
                d.scheme_code,
                keeper.scheme_code,
                d.scheme_code,
            )
            d.isin_growth = None
    return collisions


def _build_metadata_row(detail: SchemeDetail) -> dict:
    """Map one fetched scheme detail into an mf_fund_metadata upsert row."""
    category, sub_category = _split_category(detail.scheme_category)
    return {
        "scheme_code": _truncate(detail.scheme_code, 20) or detail.scheme_code,
        "isin": detail.isin_growth,
        "isin_div_reinvest": detail.isin_div_reinvest,
        "scheme_name": _truncate(detail.scheme_name, 200) or detail.scheme_name,
        "amc_name": _truncate(detail.fund_house, 100) or detail.fund_house,
        "category": _truncate(category, 50) or "Unknown",
        "sub_category": _truncate(sub_category, 100),
        "plan_type": detail.plan_type,
        "option_type": detail.option_type,
        "is_active": True,
    }


async def _upsert_metadata(
    db: AsyncSession,
    details: list[SchemeDetail],
    existing_codes: set[str],
    *,
    dry_run: bool,
) -> tuple[int, int]:
    """Upsert fund metadata in chunks and return estimated (inserted, updated) counts."""
    inserted = 0
    updated = 0
    if not details:
        return 0, 0

    rows = [_build_metadata_row(d) for d in details]
    for row in rows:
        if row["scheme_code"] in existing_codes:
            updated += 1
        else:
            inserted += 1

    if dry_run:
        return inserted, updated

    for start in range(0, len(rows), _METADATA_CHUNK):
        chunk = rows[start : start + _METADATA_CHUNK]
        stmt = pg_insert(MfFundMetadata).values(chunk)
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
    return inserted, updated


def _build_nav_rows(
    detail: SchemeDetail,
    *,
    since_exclusive: Optional[date],
) -> list[dict]:
    """Build NAV insert rows for one scheme, optionally filtering old dates."""
    mf_type = " | ".join(p for p in (detail.scheme_type, detail.scheme_category) if p) or "Unknown"
    out: list[dict] = []
    for point in detail.navs:
        if since_exclusive is not None and point.nav_date <= since_exclusive:
            continue
        out.append(
            {
                "scheme_code": detail.scheme_code,
                "isin": detail.isin_growth,
                "scheme_name": _truncate(detail.scheme_name, 200) or detail.scheme_name,
                "mf_type": _truncate(mf_type, 200) or "Unknown",
                "nav": point.nav,
                "nav_date": point.nav_date,
            }
        )
    return out


async def _insert_navs(
    db: AsyncSession,
    details: list[SchemeDetail],
    high_water: dict[str, date],
    *,
    mode: IngestMode,
    dry_run: bool,
) -> tuple[int, int, int]:
    """Insert NAV rows in chunks and return (inserted, skipped_duplicates, candidates)."""
    candidate_rows: list[dict] = []
    for d in details:
        since = high_water.get(d.scheme_code) if mode is IngestMode.INCREMENTAL else None
        candidate_rows.extend(_build_nav_rows(d, since_exclusive=since))

    total_candidates = len(candidate_rows)
    if dry_run:
        return 0, 0, total_candidates
    if not candidate_rows:
        return 0, 0, 0

    inserted_total = 0
    total_chunks = (len(candidate_rows) + _NAV_CHUNK - 1) // _NAV_CHUNK
    for chunk_idx, start in enumerate(range(0, len(candidate_rows), _NAV_CHUNK), 1):
        chunk = candidate_rows[start : start + _NAV_CHUNK]
        inserted_total += await bulk_insert_nav_rows(db, chunk)
        if total_chunks > 5 and chunk_idx % 10 == 0:
            logger.info(
                "  nav insert progress: chunk %d/%d (%d rows so far)",
                chunk_idx, total_chunks, inserted_total,
            )
    skipped = total_candidates - inserted_total
    return inserted_total, skipped, total_candidates


async def ingest_mfapi(
    db: AsyncSession,
    *,
    mode: IngestMode = IngestMode.INCREMENTAL,
    scheme_codes: Optional[list[str]] = None,
    concurrency: int = MFAPI_CONCURRENCY,
    dry_run: bool = False,
    resume_from_last_in_db: bool = False,
    metadata_only: bool = False,
) -> MfapiIngestResult:
    """Run end-to-end mfapi ingest: fetch, dedup, upsert metadata, and insert NAVs."""
    started_at = datetime.now(timezone.utc)
    result = MfapiIngestResult(
        mode=mode.value, started_at=started_at, finished_at=started_at, dry_run=dry_run
    )

    try:
        async with httpx.AsyncClient(timeout=MFAPI_TIMEOUT, follow_redirects=True) as client:
            if resume_from_last_in_db and not scheme_codes:
                codes = await _universe_codes_after_last_in_db(client, db)
            else:
                codes = await _resolve_universe_codes(client, scheme_codes)
            result.schemes_seen = len(codes)
            if not codes:
                result.finished_at = datetime.now(timezone.utc)
                return result

            details, failed = await fetch_many_scheme_details(
                client, codes, concurrency=concurrency
            )
            result.failed_codes = failed
            result.parse_errors = sum(d.parse_errors for d in details)
            logger.info(
                "  fetched %d scheme details (%d failed) — starting DB writes …",
                len(details), len(failed),
            )

            if not details:
                result.finished_at = datetime.now(timezone.utc)
                return result

            existing_codes = await _existing_scheme_codes(
                db, (d.scheme_code for d in details)
            )
            result.isin_collisions = _dedup_isin(details, existing_codes)

            high_water = (
                await _high_water_marks(db, [d.scheme_code for d in details])
                if mode is IngestMode.INCREMENTAL and not metadata_only
                else {}
            )

            inserted, updated = await _upsert_metadata(
                db, details, existing_codes, dry_run=dry_run
            )
            result.schemes_inserted = inserted
            result.schemes_updated = updated
            if metadata_only:
                logger.info(
                    "  metadata upserted (new=%d updated=%d) — metadata_only, skipping NAV insert",
                    inserted, updated,
                )
            else:
                logger.info(
                    "  metadata upserted (new=%d updated=%d) — inserting NAV rows …",
                    inserted, updated,
                )

            if metadata_only:
                result.nav_rows_inserted = 0
                result.nav_rows_candidate = 0
                result.nav_rows_skipped_duplicate = 0
            else:
                nav_inserted, nav_skipped, nav_candidates = await _insert_navs(
                    db, details, high_water, mode=mode, dry_run=dry_run
                )
                result.nav_rows_inserted = nav_inserted
                result.nav_rows_candidate = nav_candidates
                result.nav_rows_skipped_duplicate = nav_skipped

            if not dry_run:
                await db.commit()
    except MfapiFetchError as exc:
        if not dry_run:
            await db.rollback()
        raise MfapiIngestError(str(exc)) from exc
    except Exception as exc:
        if not dry_run:
            await db.rollback()
        logger.exception("mfapi ingest failed")
        raise MfapiIngestError(str(exc)) from exc

    result.finished_at = datetime.now(timezone.utc)
    logger.info(
        "mfapi ingest done mode=%s seen=%d inserted=%d updated=%d "
        "nav_candidates=%d nav_inserted=%d nav_skipped=%d collisions=%d failed=%d parse_errors=%d",
        result.mode,
        result.schemes_seen,
        result.schemes_inserted,
        result.schemes_updated,
        result.nav_rows_candidate,
        result.nav_rows_inserted,
        result.nav_rows_skipped_duplicate,
        result.isin_collisions,
        len(result.failed_codes),
        result.parse_errors,
    )
    return result


_BACKFILL_AA_SUMMARIES_ISIN = text(
    "UPDATE mf_aa_summaries s SET isin = m.isin "
    "FROM mf_fund_metadata m "
    "WHERE s.isin IS NULL AND s.scheme = m.scheme_code AND m.isin IS NOT NULL"
)
_BACKFILL_AA_TXNS_ISIN = text(
    "UPDATE mf_aa_transactions t SET isin = m.isin "
    "FROM mf_fund_metadata m "
    "WHERE t.isin IS NULL AND t.scheme = m.scheme_code AND m.isin IS NOT NULL"
)
_BACKFILL_NAV_ISIN = text(
    "UPDATE mf_nav_history h SET isin = m.isin "
    "FROM mf_fund_metadata m "
    "WHERE h.isin IS NULL AND h.scheme_code = m.scheme_code AND m.isin IS NOT NULL"
)
_BACKFILL_AA_SUMMARIES_SCHEME = text(
    "UPDATE mf_aa_summaries s SET scheme = m.scheme_code "
    "FROM mf_fund_metadata m "
    "WHERE s.scheme IS NULL AND s.isin = m.isin"
)
_BACKFILL_AA_TXNS_SCHEME = text(
    "UPDATE mf_aa_transactions t SET scheme = m.scheme_code "
    "FROM mf_fund_metadata m "
    "WHERE t.scheme IS NULL AND t.isin = m.isin"
)


async def backfill_isin_on_existing_rows(db: AsyncSession) -> BackfillIsinResult:
    """Idempotent ISIN/scheme backfill across AA imports + NAV history.

    Joins each row to ``mf_fund_metadata`` by whichever of (scheme_code, isin) is
    populated, and fills the other side. Safe to re-run.
    """
    result = BackfillIsinResult()
    try:
        r1 = await db.execute(_BACKFILL_AA_SUMMARIES_ISIN)
        result.aa_summaries_isin_filled = int(r1.rowcount or 0)
        r2 = await db.execute(_BACKFILL_AA_TXNS_ISIN)
        result.aa_transactions_isin_filled = int(r2.rowcount or 0)
        r3 = await db.execute(_BACKFILL_NAV_ISIN)
        result.nav_history_isin_filled = int(r3.rowcount or 0)
        r4 = await db.execute(_BACKFILL_AA_SUMMARIES_SCHEME)
        result.aa_summaries_scheme_filled = int(r4.rowcount or 0)
        r5 = await db.execute(_BACKFILL_AA_TXNS_SCHEME)
        result.aa_transactions_scheme_filled = int(r5.rowcount or 0)
        await db.commit()
    except Exception:
        await db.rollback()
        raise
    return result
