"""Historical backfill via VR's async bulk-request route.

The paged endpoints reach back 90 days. Everything older comes from
``/bulk-request/v1/{table}``, which returns two URLs and no rows — one paged
URL that answers ``DATA_NOT_READY`` until generation completes, one
``/download-table-data?auth-key=`` zip of CSVs. The extract is valid 24 hours
and the zip may be downloaded twice.

**The budget is the dangerous part.** VR allows two bulk generations per table
per calendar day and a burnt one is not refundable, so a retry loop that looks
harmless can cost a day of backfill for a table. :func:`reserve_bulk_request`
therefore decrements ``vr.bulk_budget`` in the same transaction that authorises
the call, and does it *before* the HTTP request rather than after — an
unreported success (timeout on a request VR actually processed) must cost the
budget, because VR counted it.
"""

from __future__ import annotations

import csv
import io
import logging
import zipfile
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Optional

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.vr_data.client import (
    BULK_REQUESTS_PER_TABLE_PER_DAY,
    VrClient,
    VrError,
    today_ist,
)
from app.domains.vr_data.schema import BULK_BUDGET, table_for
from app.domains.vr_data.services.sync_service import _chunk_size, _coerce_row
from app.domains.vr_data.specs import spec

logger = logging.getLogger(__name__)


class VrBudgetExhausted(VrError):
    """Today's two bulk requests for this table are already spent."""


@dataclass
class BackfillResult:
    table: str
    rows_written: int = 0
    files_loaded: int = 0
    coercion_failures: int = 0
    download_url: Optional[str] = None
    paged_url: Optional[str] = None
    error: Optional[str] = None
    unknown_fields: list[str] = field(default_factory=list)


async def budget_remaining(db: AsyncSession, table: str) -> int:
    row = (
        await db.execute(
            select(BULK_BUDGET.c.requests_used).where(
                BULK_BUDGET.c.table_name == table,
                BULK_BUDGET.c.budget_date == today_ist(),
            )
        )
    ).scalar_one_or_none()
    return BULK_REQUESTS_PER_TABLE_PER_DAY - int(row or 0)


async def reserve_bulk_request(db: AsyncSession, table: str) -> int:
    """Spend one of today's two bulk requests, or raise.

    Committed before the HTTP call: if the request is issued and we never see
    the response, VR has still counted it, and a mirror that thinks it has
    budget it does not is how you discover the cap by exhausting it.
    """
    today = today_ist()
    stmt = pg_insert(BULK_BUDGET).values(
        table_name=table, budget_date=today, requests_used=1
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[BULK_BUDGET.c.table_name, BULK_BUDGET.c.budget_date],
        set_={"requests_used": BULK_BUDGET.c.requests_used + 1},
        where=BULK_BUDGET.c.requests_used < BULK_REQUESTS_PER_TABLE_PER_DAY,
    ).returning(BULK_BUDGET.c.requests_used)

    used = (await db.execute(stmt)).scalar_one_or_none()
    if used is None:
        await db.rollback()
        raise VrBudgetExhausted(
            f"{table}: both bulk requests for {today.isoformat()} are spent. "
            "The cap is per table per calendar day and does not reset early."
        )
    await db.commit()
    return BULK_REQUESTS_PER_TABLE_PER_DAY - int(used)


async def start_backfill(
    db: AsyncSession,
    client: VrClient,
    table: str,
    *,
    changed_after: Optional[date] = None,
) -> BackfillResult:
    """Reserve budget and ask VR to generate the extract. Returns its URLs."""
    result = BackfillResult(table=table)
    spec(table)  # raises early on an unknown table, before spending budget
    await reserve_bulk_request(db, table)
    try:
        payload = await client.request_bulk_extract(table, changed_after=changed_after)
    except VrError as exc:
        result.error = str(exc)
        return result

    links = payload if isinstance(payload, dict) else {}
    for key, value in links.items():
        if not isinstance(value, str) or not value.startswith("http"):
            continue
        if "download-table-data" in value:
            result.download_url = value
        elif result.paged_url is None:
            result.paged_url = value
    if result.download_url:
        await db.execute(
            BULK_BUDGET.update()
            .where(
                BULK_BUDGET.c.table_name == table,
                BULK_BUDGET.c.budget_date == today_ist(),
            )
            .values(last_download_url=result.download_url)
        )
        await db.commit()
    if not (result.download_url or result.paged_url):
        result.error = f"{table}: bulk response carried no URLs: {str(payload)[:300]}"
    return result


async def load_bulk_zip(
    db: AsyncSession, table: str, download_url: str, *, timeout: float = 600.0
) -> BackfillResult:
    """Download the CSV zip and upsert every row into the mirror.

    Streamed to memory rather than disk because the deploy box is a single EC2
    instance with no scratch volume we control; if a table ever outgrows that,
    the paged URL is the alternative and needs no new budget.
    """
    result = BackfillResult(table=table, download_url=download_url)
    spec_ = spec(table)
    target = table_for(table)
    types = spec_.column_types()
    known = set(spec_.columns)
    chunk = _chunk_size(len(spec_.columns) + 1)
    unknown: set[str] = set()

    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as http:
            response = await http.get(download_url)
            response.raise_for_status()
            blob = response.content
    except httpx.HTTPError as exc:
        result.error = f"{table}: bulk download failed: {exc}"
        return result

    try:
        archive = zipfile.ZipFile(io.BytesIO(blob))
    except zipfile.BadZipFile:
        result.error = f"{table}: bulk download was not a zip (expired auth-key?)"
        return result

    for member in archive.namelist():
        if not member.lower().endswith(".csv"):
            continue
        with archive.open(member) as handle:
            reader = csv.DictReader(io.TextIOWrapper(handle, encoding="utf-8-sig"))
            unknown |= set(reader.fieldnames or []) - known
            batch: list[dict[str, Any]] = []
            for raw in reader:
                row, failures = _coerce_row(raw, types)
                result.coercion_failures += failures
                if any(row.get(k) is None for k in spec_.primary_key):
                    result.coercion_failures += 1
                    continue
                batch.append(row)
                if len(batch) >= chunk:
                    await _upsert(db, target, spec_, batch)
                    result.rows_written += len(batch)
                    batch = []
            if batch:
                await _upsert(db, target, spec_, batch)
                result.rows_written += len(batch)
        result.files_loaded += 1

    await db.commit()
    result.unknown_fields = sorted(unknown)
    if unknown:
        logger.warning(
            "vr backfill %s: %d CSV column(s) absent from catalog.json (%s)",
            table,
            len(unknown),
            ", ".join(sorted(unknown)[:8]),
        )
    return result


async def _upsert(db: AsyncSession, target, spec_, batch: list[dict[str, Any]]) -> None:
    stmt = pg_insert(target).values(batch)
    await db.execute(
        stmt.on_conflict_do_update(
            index_elements=list(spec_.primary_key),
            set_={
                c: stmt.excluded[c]
                for c in spec_.columns
                if c not in spec_.primary_key
            },
        )
    )
