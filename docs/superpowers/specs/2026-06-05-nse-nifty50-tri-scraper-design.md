# NSE Nifty 50 TRI Scraper — Design Spec

**Date:** 2026-06-05
**Status:** Approved design, pending spec review
**Scope:** Part 1 of 2. This spec covers **only** fetching + storing + auto-refreshing
the NSE Nifty 50 Total Return Index (TRI) history. The benchmarking *consumer*
(Part 2 — replaying customer transactions against the TRI) is **out of scope** here
and will get its own spec.

---

## 1. Goal

Maintain a local, queryable history of the **Nifty 50 TRI** so that a later
benchmarking module can value a hypothetical "same money invested in Nifty 50"
position on any customer purchase date.

Why TRI (not the price index): SEBI mandates Total Return Index as the official
benchmark for mutual fund performance
([SEBI circular, 04-Jan-2018](https://www.sebi.gov.in/legal/circulars/jan-2018/benchmarking-of-scheme-s-performance-to-total-return-index_37273.html)).
The price index excludes dividends and understates the benchmark by ~1.3%/yr
(measured: 11.03% vs 12.39% CAGR over 10y), which would make every customer look
like they beat the market.

## 2. Scope

**In scope (Part 1):**
- One index only: **Nifty 50 TRI** (table designed so more indices can be added later without schema change).
- Full available history backfill.
- Daily automatic refresh.
- A read accessor for Part 2 to consume.

**Out of scope (Part 2, later):** the benchmarking calculation, any chart, any API
router/endpoint for customers. No consumer is built now.

## 3. Verified facts (tested against the live endpoint)

- **Endpoint:** `POST https://www.niftyindices.com/Backpage.aspx/getTotalReturnIndexString`
- **Required headers:** `Content-Type: application/json; charset=UTF-8`, `User-Agent` (browser UA), `Referer: https://www.niftyindices.com/reports/historical-data`, `X-Requested-With: XMLHttpRequest`
- **Payload:** `{"cinfo": "{'name':'NIFTY 50','startDate':'DD-MMM-YYYY','endDate':'DD-MMM-YYYY','indexName':'NIFTY 50'}"}`
- **Response:** JSON string in `d`; each record has `Date` (`"DD Mon YYYY"`), `TotalReturnsIndex`, `NTR_Value`. Records returned newest-first, values are strings.
- **Earliest available date:** `30-Jun-1999` (TRI = 1256.38).
- **Spot-check anchor:** 31-Jan-2024 → TRI = **31939.59**, NTR = 28933.54.
- **Reliability:** long single-range requests **time out**. Must chunk + retry.

## 4. Architecture

Lives in `app/domains/mutual_funds/`, mirroring the existing mfapi NAV machinery
(`mfapi_fetcher.py` HTTP layer ↔ `nav_history_service.py` DB layer ↔
`mfapi_scheduler.py` cron).

### New files
```
app/domains/mutual_funds/models/index_tri_history.py     # ORM table
app/domains/mutual_funds/schemas/index_tri.py            # pydantic
app/domains/mutual_funds/services/niftyindices_fetcher.py # HTTP client (retry + chunking)
app/domains/mutual_funds/services/index_tri_service.py    # DB: backfill / incremental / read accessor + job fn
app/domains/mutual_funds/services/index_tri_scheduler.py  # independent AsyncIOScheduler
```

### Edited files
```
app/core/lifespan.py                              # start/stop new scheduler in _start_schedulers() + shutdown path
app/core/config.py                                # add index_tri_scheduler_enabled()
app/domains/mutual_funds/models/__init__.py       # export IndexTriHistory
app/domains/mutual_funds/schemas/__init__.py      # export new schemas
alembic/ (new migration)                          # create index_tri_history table
```

## 5. Data model — `index_tri_history`

| Column | Type | Notes |
|---|---|---|
| `id` | UUID | PK (match existing UUID PK pattern) |
| `index_name` | String(50) | `"NIFTY 50"`; keeps table multi-index ready |
| `tri_date` | Date | indexed |
| `tri_value` | Numeric(14,4) | **Gross TRI** — the benchmark value |
| `ntr_value` | Numeric(14,4) | Net TRI (stored for free from same response) |
| `created_at` | DateTime(tz) | server default `now()` |

- **`UniqueConstraint(index_name, tri_date)`** named `uq_index_tri_name_date` — idempotent re-runs (mirrors `uq_mf_nav_scheme_date`).
- Column named `tri_value` (lowercase) for consistency with sibling `ntr_value` and existing columns (`nav`, `nav_date`).

## 6. Fetcher — `niftyindices_fetcher.py`

Pure HTTP, no DB. Mirrors `mfapi_fetcher.py`.

- `fetch_tri(index_name, start: date, end: date) -> list[TriRow]`
  - Builds the `cinfo` payload, sends POST with required headers.
  - `tries=3` with backoff on timeout/non-200 (same shape as existing fetchers).
  - Parses `d` JSON → typed rows: `(tri_date, tri_value, ntr_value)`; casts strings → date/float; sorts ascending.
- `fetch_tri_chunked(index_name, start, end, window_years=2) -> list[TriRow]`
  - Splits `[start, end]` into ≤2-year windows (long ranges time out), calls `fetch_tri` per window, concatenates, dedupes by date.

## 7. Service — `index_tri_service.py`

DB orchestration + the scheduled job function. Mirrors `nav_history_service`.

- `bulk_insert_tri_rows(db, index_name, rows) -> int` — insert with ON CONFLICT DO NOTHING on `(index_name, tri_date)`.
- `backfill_full_history(db, index_name="NIFTY 50") -> int` — fetch `30-Jun-1999 → today` via `fetch_tri_chunked`, bulk insert. Safe to re-run.
- `refresh_incremental(db, index_name="NIFTY 50") -> int` — read high-water mark `max(tri_date)`; fetch `(hwm+1 → today)`; bulk insert. If table empty → delegates to `backfill_full_history`.
- `get_tri_on_or_before(db, index_name, on: date) -> IndexTriHistory | None` — nearest trading-day row ≤ `on` (the accessor Part 2 needs).
- `run_tri_refresh_job() -> None` — async entry the scheduler calls: opens a session, acquires a **Postgres advisory lock** (new key, distinct from `MFAPI_LOCK_KEY`), runs `refresh_incremental`, releases lock. All TRI logic stays in this module.

## 8. Scheduler — `index_tri_scheduler.py` (independent)

A **separate** `AsyncIOScheduler` from `mfapi_scheduler`, decoupled by design.

- `start_tri_scheduler() -> scheduler | None` — create `AsyncIOScheduler`, register one `CronTrigger` job calling `run_tri_refresh_job`, start, return instance.
- `shutdown_tri_scheduler() -> None`.
- **Cron time:** **20:30 IST** — NSE publishes TRI after market close, so an evening pull captures same-day data. First run on an empty table performs the full backfill; later runs are incremental.
- **Env gate:** `config.py::index_tri_scheduler_enabled()` (default ON; `INDEX_TRI_SCHEDULER_ENABLED=false` to disable in tests/dev) — same shape as `mfapi_scheduler_enabled()`.
- **Wiring:** add start/stop calls into `app/core/lifespan.py::_start_schedulers()` and the shutdown path, alongside the existing scheduler.

## 9. Migration

- One Alembic revision creating `index_tri_history` with the unique constraint and the `tri_date` index. No backfill in the migration — data load happens via the service (first scheduler run or a manual `backfill_full_history` call).

## 10. Verification / success criteria

1. **Backfill correctness:** after `backfill_full_history`, earliest `tri_date == 1999-06-30`; row count ≈ 6,500; a spot-checked month has no missing trading days.
2. **Anchor value:** row for `2024-01-31` has `tri_value ≈ 31939.59`.
3. **Idempotency:** re-running backfill inserts **0** duplicate rows (unique constraint holds).
4. **Incremental:** with the latest N days deleted, `refresh_incremental` re-inserts exactly those N days and nothing else.
5. **Accessor:** `get_tri_on_or_before(db, "NIFTY 50", <a Sunday>)` returns the preceding Friday's row.
6. **Scheduler:** with the env flag off, no TRI job registers; with it on, exactly one job is registered and the app starts/stops cleanly.

## 11. Assumptions & decisions

- Single index (Nifty 50) for now — chosen by user; table is multi-index ready.
- Store both Gross TRI and NTR; benchmark on Gross.
- Full history (not just 10y) — so any customer purchase date is covered.
- Separate fetcher file and separate scheduler — chosen by user for separation /
  consistency with `mfapi_fetcher.py`.
- Unofficial NSE endpoint is acceptable as the source (same posture as the
  existing mfapi.in dependency); retry + chunking mitigate flakiness.
```
