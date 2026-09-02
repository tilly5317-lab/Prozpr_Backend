"""Generic incremental sync — one implementation for every mirrored table.

The loop is the same whatever the table, which is the point of the spec
registry: read the watermark, ask VR for everything changed since, upsert by
the spec's primary key, advance the watermark, record the outcome. Per-table
behaviour lives in :mod:`..specs`, never here.

Three things are load-bearing and easy to get subtly wrong:

* **The watermark advances only after the rows are committed.** A crash
  mid-walk therefore re-reads one window rather than skipping it. Duplicate
  work is free (the upsert is idempotent); a gap is invisible forever.
* **Rows are coerced, not trusted.** VR sends everything as strings, including
  ``""`` for null. :func:`_coerce_row` maps blanks to ``NULL`` and parses the
  handful of columns the spec types as date/numeric/json. A value that will not
  parse is stored as ``NULL`` and counted, not raised — one malformed field in
  one plan must not abort a 5000-row page.
* **Every page is chunked below asyncpg's 32767-parameter ceiling.** The same
  limit that forced `mf_transactions` inserts to 1500 rows applies here, and an
  83-column table hits it at ~390 rows.

``deleted_logs`` is not a mirror in the usual sense: :func:`apply_deletions`
reads it and *removes* rows from the other tables. It runs last in every cycle,
after the tables it prunes have been refreshed.
"""

from __future__ import annotations

import json
import logging
import zlib
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, AsyncIterator, Optional

from sqlalchemy.exc import SQLAlchemyError

from sqlalchemy import delete, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.domains.vr_data.client import (
    MAX_INCREMENTAL_WINDOW_DAYS,
    VrAccessError,
    VrClient,
    VrError,
    today_ist,
)
from app.domains.vr_data.schema import SYNC_STATE, table_for
from app.domains.vr_data.specs import VrTableSpec, spec, specs_for_tiers

logger = logging.getLogger(__name__)

#: asyncpg refuses a statement with more than 32767 bound parameters. Chunk so
#: even the widest table (fund_basic_details, 83 columns + _vr_synced_at) stays
#: comfortably under it.
_MAX_BIND_PARAMS = 30_000

#: Overlap re-read on every incremental run. VR stamps changes in IST and our
#: watermark is a date, so a run at 02:00 that asked for "changed after
#: yesterday" would miss anything VR stamped late on the boundary day.
_WATERMARK_OVERLAP_DAYS = 2

_TRUE_NULLS = {"", "-", "na", "n/a", "null", "none"}

#: One advisory lock per mirrored table, so two uvicorn workers (or a scheduler
#: and an ops call) never write the same table concurrently. Per-table rather
#: than global: a slow holdings walk must not block the NAV pull.
_LOCK_NAMESPACE = 7421200

#: Ceilings applied to the sync session only. ``lock_timeout`` turns a blocked
#: write into a fast, retryable error instead of a stalled connection, and
#: ``idle_in_transaction_session_timeout`` guarantees a crashed sync cannot pin
#: a transaction open and block autovacuum on a live database.
_LOCK_TIMEOUT_MS = 5_000
_STATEMENT_TIMEOUT_MS = 120_000
_IDLE_TX_TIMEOUT_MS = 60_000


def _table_lock_key(table: str) -> int:
    """Stable 32-bit lock id for a table name."""
    return _LOCK_NAMESPACE + (zlib.crc32(table.encode()) % 100_000)


async def _apply_session_guards(db: AsyncSession) -> None:
    """Bound how long this session can hold or wait for a lock.

    ``SET LOCAL`` scopes these to the current transaction, so nothing here
    leaks into the pooled connection's next user — a request handler must not
    inherit a 120s statement timeout because a sync borrowed the connection.
    """
    await db.execute(text(f"SET LOCAL lock_timeout = '{_LOCK_TIMEOUT_MS}ms'"))
    await db.execute(text(f"SET LOCAL statement_timeout = '{_STATEMENT_TIMEOUT_MS}ms'"))
    await db.execute(
        text(
            "SET LOCAL idle_in_transaction_session_timeout = "
            f"'{_IDLE_TX_TIMEOUT_MS}ms'"
        )
    )


@asynccontextmanager
async def _table_lock(db: AsyncSession, table: str) -> AsyncIterator[bool]:
    """Hold the per-table advisory lock, or yield ``False`` if someone else has it.

    ``pg_try_advisory_lock`` rather than the blocking form: a second writer
    should skip this cycle and try again on the next one, not queue up behind
    a 40-minute holdings backfill holding a connection open.
    """
    key = _table_lock_key(table)
    acquired = bool(
        (
            await db.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": key})
        ).scalar()
    )
    try:
        yield acquired
    finally:
        if acquired:
            try:
                await db.execute(
                    text("SELECT pg_advisory_unlock(:k)"), {"k": key}
                )
            except SQLAlchemyError:
                logger.warning(
                    "vr sync %s: failed to release advisory lock", table, exc_info=True
                )


@dataclass
class SyncResult:
    """Outcome of one table's sync. Returned to the ops endpoint verbatim."""

    table: str
    rows_written: int = 0
    pages: int = 0
    watermark: Optional[str] = None
    coercion_failures: int = 0
    skipped_reason: Optional[str] = None
    error: Optional[str] = None
    access_denied_by_vendor: bool = False
    fields_dropped: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.error is None


# ---------------------------------------------------------------------------
# coercion
# ---------------------------------------------------------------------------


def _parse_date(raw: str) -> Optional[date]:
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d", "%d-%b-%Y"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def _parse_timestamp(raw: str) -> Optional[datetime]:
    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%d-%H-%M",
        "%Y-%m-%d",
    ):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


def _coerce_row(
    row: dict[str, Any], types: dict[str, str]
) -> tuple[dict[str, Any], int]:
    """Map one VR record onto the mirror's columns.

    Unknown keys are dropped by the caller (which reports them once per run
    rather than per row — a new VR field should be a visible signal to update
    ``catalog.json``, not 5000 log lines).
    """
    out: dict[str, Any] = {}
    failures = 0
    for column, kind in types.items():
        value = row.get(column)
        if value is None:
            out[column] = None
            continue
        if isinstance(value, str):
            stripped = value.strip()
            if stripped.lower() in _TRUE_NULLS:
                out[column] = None
                continue
            value = stripped

        if kind == "jsonb":
            if isinstance(value, (dict, list)):
                out[column] = value
            elif isinstance(value, str):
                try:
                    out[column] = json.loads(value)
                except ValueError:
                    # Keep it rather than lose it: a string is valid JSON.
                    out[column] = value
            else:
                out[column] = value
        elif kind == "date":
            parsed = _parse_date(str(value)) if not isinstance(value, date) else value
            if parsed is None:
                failures += 1
            out[column] = parsed
        elif kind == "timestamptz":
            parsed = (
                _parse_timestamp(str(value))
                if not isinstance(value, datetime)
                else value
            )
            if parsed is None:
                failures += 1
            out[column] = parsed
        elif kind == "numeric":
            try:
                out[column] = Decimal(str(value).replace(",", ""))
            except (InvalidOperation, ValueError):
                failures += 1
                out[column] = None
        elif kind == "integer":
            try:
                out[column] = int(Decimal(str(value)))
            except (InvalidOperation, ValueError):
                failures += 1
                out[column] = None
        else:
            out[column] = str(value)
    return out, failures


def _chunk_size(column_count: int) -> int:
    return max(1, _MAX_BIND_PARAMS // max(1, column_count))


# ---------------------------------------------------------------------------
# watermark bookkeeping
# ---------------------------------------------------------------------------


async def get_state(db: AsyncSession, table: str) -> Optional[dict[str, Any]]:
    row = (
        await db.execute(select(SYNC_STATE).where(SYNC_STATE.c.table_name == table))
    ).mappings().first()
    return dict(row) if row else None


async def _mark_running(db: AsyncSession, table: str) -> None:
    stmt = pg_insert(SYNC_STATE).values(
        table_name=table, last_run_at=datetime.now(), last_status="running"
    )
    await db.execute(
        stmt.on_conflict_do_update(
            index_elements=[SYNC_STATE.c.table_name],
            set_={"last_run_at": stmt.excluded.last_run_at, "last_status": "running"},
        )
    )
    await db.commit()


async def _record_outcome(
    db: AsyncSession, result: SyncResult, *, total_rows: Optional[int]
) -> None:
    values: dict[str, Any] = {
        "last_run_at": datetime.now(),
        "last_status": "ok" if result.ok else "error",
        "last_error": result.error,
        "last_row_count": result.rows_written,
        "last_page_count": result.pages,
    }
    if result.ok:
        values["last_success_at"] = datetime.now()
        if result.watermark:
            values["watermark"] = result.watermark
        if total_rows is not None:
            values["total_rows"] = total_rows
    await db.execute(
        update(SYNC_STATE)
        .where(SYNC_STATE.c.table_name == result.table)
        .values(**values)
    )
    await db.commit()


def _window_start(spec_: VrTableSpec, watermark: Optional[str]) -> Optional[date]:
    """Where this run starts reading.

    No watermark means a first run: VR's paged endpoint reaches back 90 days,
    so we take all of it and leave anything older to the bulk backfill. With a
    watermark we re-read a two-day overlap (see ``_WATERMARK_OVERLAP_DAYS``).
    """
    horizon = today_ist() - timedelta(days=MAX_INCREMENTAL_WINDOW_DAYS - 1)
    if not watermark:
        return horizon
    parsed = _parse_date(watermark)
    if parsed is None:
        return horizon
    return max(horizon, parsed - timedelta(days=_WATERMARK_OVERLAP_DAYS))


def _highest_watermark(
    rows: list[dict[str, Any]], column: str, current: Optional[str]
) -> Optional[str]:
    best = _parse_date(current) if current else None
    for row in rows:
        value = row.get(column)
        if isinstance(value, datetime):
            value = value.date()
        if isinstance(value, date) and (best is None or value > best):
            best = value
    return best.isoformat() if best else current


# ---------------------------------------------------------------------------
# the sync
# ---------------------------------------------------------------------------


async def sync_table(
    db: AsyncSession,
    client: VrClient,
    table: str,
    *,
    max_pages: int = 200,
    since: Optional[date] = None,
) -> SyncResult:
    """Pull one table up to date and upsert it into ``vr.<table>``.

    Serialized per table by a Postgres advisory lock, so a scheduler run and a
    manually triggered ops run cannot interleave writes to the same table.
    """
    spec_ = spec(table)
    result = SyncResult(table=table)

    if not client.configured:
        result.skipped_reason = "VR_API_KEY not set"
        return result

    async with _table_lock(db, table) as acquired:
        if not acquired:
            result.skipped_reason = "another worker is syncing this table"
            return result
        return await _sync_table_locked(
            db, client, spec_, result, max_pages=max_pages, since=since
        )


async def _sync_table_locked(
    db: AsyncSession,
    client: VrClient,
    spec_: VrTableSpec,
    result: SyncResult,
    *,
    max_pages: int,
    since: Optional[date],
) -> SyncResult:
    table = spec_.name

    target = table_for(table)
    types = spec_.column_types()
    known = set(spec_.columns)
    chunk = _chunk_size(len(spec_.columns) + 1)

    state = await get_state(db, table)
    watermark = state.get("watermark") if state else None
    await _mark_running(db, table)

    if spec_.sync_mode == "full":
        window_start = None
    else:
        window_start = since or _window_start(spec_, watermark)

    unseen_fields: set[str] = set()
    page_url: Optional[str] = None
    total_rows: Optional[int] = None

    try:
        for page_no in range(max_pages):
            page = await client.fetch_page(
                table,
                changed_after=window_start if page_no == 0 else None,
                url=page_url,
            )
            result.pages += 1
            if not page.rows:
                break

            unseen_fields |= set(page.rows[0].keys()) - known
            coerced: list[dict[str, Any]] = []
            for raw in page.rows:
                row, failures = _coerce_row(raw, types)
                result.coercion_failures += failures
                if any(row.get(k) is None for k in spec_.primary_key):
                    # A row with no key cannot be upserted or later deleted.
                    result.coercion_failures += 1
                    continue
                coerced.append(row)

            # Re-applied per page: SET LOCAL is transaction-scoped and the
            # previous page's commit ended that transaction. One page = one
            # transaction is also what keeps lock hold times short.
            await _apply_session_guards(db)
            for start in range(0, len(coerced), chunk):
                batch = coerced[start : start + chunk]
                # Deterministic key order across every writer, so two syncs of
                # overlapping windows can never deadlock by grabbing the same
                # rows in opposite orders.
                batch.sort(key=lambda r: tuple(str(r[k]) for k in spec_.primary_key))
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
            await db.commit()
            result.rows_written += len(coerced)

            if spec_.watermark_column:
                watermark = _highest_watermark(
                    coerced, spec_.watermark_column, watermark
                )

            page_url = page.next_url
            if not page_url:
                break
        else:
            logger.warning(
                "vr sync %s stopped at the %d-page cap with more pages waiting; "
                "the next run resumes from the watermark.",
                table,
                max_pages,
            )

        # A full-mode table has no date column to watermark, so stamp the run
        # date instead — it is what the ops view reads for freshness.
        result.watermark = watermark or today_ist().isoformat()
        result.fields_dropped = sorted(unseen_fields)
        if unseen_fields:
            logger.warning(
                "vr sync %s: VR returned %d field(s) not in catalog.json (%s). "
                "Data was stored for known fields only — refresh the catalogue.",
                table,
                len(unseen_fields),
                ", ".join(sorted(unseen_fields)[:8]),
            )
    except VrAccessError as exc:
        await db.rollback()
        result.error = str(exc)
        result.access_denied_by_vendor = exc.reached_vr
    except (VrError, Exception) as exc:  # noqa: BLE001 - recorded, not swallowed
        await db.rollback()
        result.error = f"{type(exc).__name__}: {exc}"
        logger.exception("vr sync failed for %s", table)

    await _record_outcome(db, result, total_rows=total_rows)
    return result


# ---------------------------------------------------------------------------
# deletions
# ---------------------------------------------------------------------------


async def apply_deletions(db: AsyncSession, *, limit: int = 20_000) -> dict[str, int]:
    """Remove rows VR has retired, per the mirrored ``deleted_logs``.

    ``deleted_logs`` stores a deleted row's identity as up to ten
    ``keyN``/``valueN`` pairs — column name and value — so a delete is
    reconstructed rather than declared. Entries naming a table we do not mirror
    are ignored (they are the rest of VR's 77-table catalogue), and entries
    whose key columns are not this table's primary key are skipped rather than
    guessed at: deleting on a partial key would take out siblings.
    """
    logs = table_for("deleted_logs")
    rows = (
        (
            await db.execute(
                select(logs).order_by(logs.c.deleted_ts.asc().nullslast()).limit(limit)
            )
        )
        .mappings()
        .all()
    )

    removed: dict[str, int] = {}
    specs_by_name = {s.name: s for s in specs_for_tiers(_all_tiers())}
    for row in rows:
        target_name = (row.get("table_name") or "").strip()
        target_spec = specs_by_name.get(target_name)
        if target_spec is None or target_name == "deleted_logs":
            continue

        criteria: dict[str, str] = {}
        for n in range(1, 11):
            key = row.get(f"key{n}")
            value = row.get(f"value{n}")
            if key:
                criteria[str(key).strip()] = value
        if not criteria:
            continue
        if set(target_spec.primary_key) - set(criteria):
            logger.warning(
                "vr deleted_logs %s: keys %s do not cover %s's primary key %s; "
                "skipping rather than deleting on a partial key.",
                row.get("log_id"),
                sorted(criteria),
                target_name,
                list(target_spec.primary_key),
            )
            continue

        target = table_for(target_name)
        stmt = delete(target)
        for column in target_spec.primary_key:
            stmt = stmt.where(target.c[column] == criteria[column])
        outcome = await db.execute(stmt)
        removed[target_name] = removed.get(target_name, 0) + (outcome.rowcount or 0)

    await db.commit()
    return removed


def _all_tiers() -> tuple[str, ...]:
    return ("core", "additional", "optional", "support", "candidate")


# ---------------------------------------------------------------------------
# a full cycle
# ---------------------------------------------------------------------------


def enabled_specs() -> list[VrTableSpec]:
    """Tables this deployment syncs, per ``VR_SYNC_TIERS``."""
    return specs_for_tiers(get_settings().get_vr_sync_tiers())


async def run_cycle(
    db: AsyncSession,
    *,
    tables: Optional[list[str]] = None,
    hour: Optional[int] = None,
) -> list[SyncResult]:
    """Sync every enabled table, then apply deletions.

    ``hour`` restricts the run to tables scheduled for that IST hour, which is
    how the scheduler keeps the daily NAV pull from dragging the monthly
    holdings walk along with it.
    """
    chosen = enabled_specs()
    if tables:
        wanted = set(tables)
        chosen = [s for s in chosen if s.name in wanted]
    elif hour is not None:
        chosen = [s for s in chosen if hour in s.schedule_hours]

    results: list[SyncResult] = []
    async with VrClient() as client:
        if not client.configured:
            return [
                SyncResult(table=s.name, skipped_reason="VR_API_KEY not set")
                for s in chosen
            ]
        for spec_ in chosen:
            results.append(await sync_table(db, client, spec_.name))

    if any(r.table == "deleted_logs" and r.ok for r in results):
        try:
            removed = await apply_deletions(db)
            if removed:
                logger.info("vr deletions applied: %s", removed)
        except Exception:  # noqa: BLE001 - a failed prune must not fail the cycle
            logger.exception("vr deletion pass failed")
    return results
