# app/domains/vr_data/ — Value Research mirror

Mirrors Value Research's mutual-fund reference data into a **separate Postgres
schema (`vr`) in the same database**, on a schedule, and exposes it to the rest
of the backend through one read module. VR's own guidance is to sync into our
own store and never put their API on a user-facing read path; that is what this
domain is.

## Entry / contract

- **Write side:** `services/sync_service.run_cycle(db)` — sync every enabled
  table, then apply deletions. The scheduler calls it per IST hour.
- **Read side:** `services/vr_read_service` — takes our `scheme_code`, returns
  `None`/`[]` whenever VR has nothing. **Never** returns a default or a stale
  substitute, so a caller can add `vr_x or existing_x` and behave exactly as
  today until the mirror is populated.
- **Ops:** `/api/v1/vr/*` — status, probe, sync, crosswalk, backfill budget.
- Inert without `VR_API_KEY`: every path returns "not configured" rather than
  raising, and the scheduler refuses to start.

## Layers

- `specs.py` — the registry. One `VrTableSpec` per VR table: key, tier,
  watermark column, schedule, indexes. Column names come from `catalog.json`
  (VR's published field reference). **Adding a table is a spec entry, not code.**
- `catalog.json` — 30 tables / 499 fields, trimmed from VR's 77-table
  catalogue. The source of truth for column names.
- `schema.py` — Core `Table` objects on a private `MetaData(schema="vr")`, plus
  the three control tables and the DDL generator.
- `client.py` — the HTTP client; every VR contract quirk is encoded here.
- `services/sync_service.py` — generic incremental upsert + `deleted_logs`
  consumer.
- `services/backfill_service.py` — the >90-day bulk route and its budget guard.
- `services/crosswalk_service.py` — `plan_id` ↔ `scheme_code` resolution.
- `services/vr_read_service.py` — the additive read API.
- `scheduler.py` — APScheduler, IST, gated by `VR_SYNC_ENABLED`.

## Gotchas & invariants

- **Nothing here is on `Base.metadata`** (`schema.py:VR_METADATA`). That is what
  keeps `create_all_tables()` and Alembic autogenerate from ever seeing a vendor
  table next to a user table. `tests/test_vr_schema_isolation.py` fails the
  build if one leaks.
- **No foreign key crosses the boundary**, in either direction. `vr.scheme_link`
  is a join key, not a constraint, so a bad sync cannot cascade into holdings
  and `DROP SCHEMA vr CASCADE` reverses everything.
- **Core (not ORM) access** — Core statements bypass the `cas_scope`
  `do_orm_execute`/`before_flush` listeners, so a CAS snapshot scope can never
  silently filter vendor rows.
- **`output` defaults to `count` at VR** (`client.py`), so `fetch_page` always
  sends `output=data`. A "silent" endpoint is usually just this.
- **A 403 has two completely different meanings** (`client.VrAccessError`). HTML
  body = Cloudflare, the request never reached VR: wrong key or non-whitelisted
  IP (ours is the backend, `13.234.33.230`). JSON body = VR refusing that table
  for our key: a contract question, not retryable.
- **`changed-after` cannot exceed 90 days**; older data needs
  `bulk-request`, which is **capped at 2 per table per calendar day and does not
  refund**. `backfill_service.reserve_bulk_request` decrements `vr.bulk_budget`
  *before* the HTTP call, because VR counts a request we never see the answer to.
- **The watermark advances only after rows commit** (`sync_service`). A crash
  re-reads one window; it never leaves a gap. A 2-day overlap covers VR's IST
  change stamps.
- **`deleted_logs` is mandatory, and runs last.** Without it, deletions at VR
  never reach the mirror and it drifts with no error anywhere. It is pinned to
  the `support` tier so no `VR_SYNC_TIERS` value can drop it while keeping the
  tables it prunes.
- **Ids and flags stay `TEXT`** (`specs.infer_column_type`). VR promises only
  "unique identifier"; a `Y`/`N` in a `BOOLEAN` or an alphanumeric id in a
  `BIGINT` fails a whole 5000-row page, while a `TEXT` column that turns out
  numeric costs one `ALTER`.
- **Per-table advisory lock + `SET LOCAL` timeouts** (`sync_service._table_lock`,
  `_apply_session_guards`). Locks are per table so a monthly holdings walk cannot
  block the daily NAV pull; batches are sorted by primary key so two overlapping
  syncs cannot deadlock; `SET LOCAL` keeps the timeouts from leaking into the
  next user of a pooled connection.
- **Crosswalk never matches on scheme name.** ISIN and AMFI code only. Name
  matching across "Direct Growth"/"Direct - Growth" is how one fund's rating
  gets attached to another. Unresolved = absent, and absent = no VR data.
- **`fund_transaction_details` is documented by VR as *irregularly* updated**, so
  `vr_read_service.transactability` returns a `freshness_warning` with it. Use it
  to stop proposing a closed scheme; the RTA stays the authority at order time.

## Testing

`.venv/Scripts/python -m pytest app/domains/vr_data/tests/` — 41 tests, no
network and no database. `test_vr_schema_isolation.py` asserts the safety
claims themselves (no `Base.metadata` leak, no FKs, re-runnable DDL, scope
pinned to the CFO's list); treat a failure there as a blocked deploy, not a
flaky test.

Live checks run **on the whitelisted backend only**:
`python -m scripts.vr_smoke_test --tier core` (one `output=count` call per
table, writes nothing).

## Don't read

- `catalog.json` — 60 KB of vendor field descriptions; query it through
  `specs.all_specs()` rather than reading it.
