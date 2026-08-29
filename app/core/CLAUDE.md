# app/core/ — Cross-cutting infrastructure

No business logic — only the infra every domain depends on.

## Files

- `config.py` — `get_settings()`: DB URL, `JWT_SECRET`, `ENCRYPTION_KEY`, CORS origins, `DEPLOY_ENV`, per-feature Anthropic keys with `ANTHROPIC_API_KEY` as shared fallback. `_backend_dir` is `parents[2]` (repo root) — it was `parents[1]` and only worked because PM2 sets `cwd` there.
- `database.py` — `Base` (`DeclarativeBase`), async engine + session factory, `get_db()` dependency, `create_all_tables`, `dispose_engine`, `apply_postgres_schema_patches`.
- `cas_scope.py` — CAS snapshot scoping. A `do_orm_execute` hook restricts every ORM SELECT over a `CasScoped` table to the request's active `cas_uploads` row, and a `before_flush` hook stamps that id onto every new `CasScoped` row. `scoped_to` / `unscoped` / `cas_scope_for_user` set the scope; `scope_filter` / `non_snapshot_filter` confine bulk DELETEs (the hooks only touch SELECTs).
- `dependencies.py` — `get_current_user` JWT auth; `get_effective_user` family-member resolver (reads `X-Family-Member-Id` header); `get_ai_user_context` User-with-relations loader for AI handlers.
- `security.py` — password hashing (`bcrypt`) + JWT encode/decode.
- `lifespan.py` — FastAPI startup/shutdown; `_start_schedulers()` starts/stops background schedulers, each gated by its own env flag (`MFAPI_SCHEDULER_ENABLED`, `BENCHMARK_SCHEDULER_ENABLED` — legacy alias `INDEX_TRI_SCHEDULER_ENABLED` still honoured, `config.py:387`).
- `exceptions.py` — `register_exception_handlers(app)` (called once from `app/main.py`) maps known errors to stable JSON: `ValidationError` → 422 (logs field NAMES only — `str(exc)` embeds submitted values); `ClientDisconnect` → 499; DB auth / host-unreachable / connection-closed → 503; else → 500 reported to PostHog via `capture_exception()`.
- `observability.py` — PostHog client lifecycle, `capture_exception()` for 5xx, and `capture_http_request()` + the two OTel hooks that emit the durable per-request event.
- `otel.py` — OTel bootstrap: tracer + logger providers exporting over OTLP to PostHog (`/i/v1/traces`, `/i/v1/logs`), authenticated with the same `phc_` project token. `attach_otel_logging()` ships stdlib logs at WARNING+; `_ServedPathSampler` (wrapped in `ParentBased`) keeps unserved paths out of the span pipeline; `shutdown_otel()` flushes both.
- `job_tracing.py` — spans + Error Tracking for background jobs: `traced_job` (decorator, no re-indent), `job_span` (run/phase), `report_job_failure` (for already-caught exceptions), and a re-export of `suppress_instrumentation`.
- `log_scrubber.py` — `LogRecordProcessor` redacting phone/PAN/email from log bodies before export. Defence-in-depth; call sites still must not log PII.
- `progress.py` — in-process progress store: long synchronous computes (rebalancing plan, SIP fund split) write stage/% here for a companion polling GET. Single-instance; entries expire after `_TTL_S` so a crashed compute never reads as stuck.

## Gotchas & invariants

- **`FastAPIInstrumentor.instrument_app()` must stay at module scope in `app/main.py`.** Inside the lifespan it is a SILENT no-op — Starlette caches `middleware_stack` on the first `__call__`, which the lifespan scope itself triggers (`test_otel_instrumentation_slot.py`).
- **Spans and events are two pipelines on purpose.** OTLP spans/logs retain ~14 days; events retain 12 months. `http_request` and `$ai_generation` are events *because* they need long history — do not "consolidate" them into spans.
- **`path` is always the route template**, never `request.url.path` — 58 of 222 routes are parameterised, and raw paths put user IDs into analytics properties (`observability.py:capture_http_request`).
- **Request duration comes from `scope[_START_NS_KEY]`, not the hook's span.** `client_response_hook` receives the `http send` CHILD span; timing off it reports ~0.01ms for a 50ms request. `otel_request_hook` stamps the server span's start onto the scope.
- **`attach_otel_logging()` attaches to `""` and `uvicorn`, NOT `uvicorn.error`** — the latter has no `propagate` override in uvicorn's `LOGGING_CONFIG` and bubbles up, so both would export every traceback twice (`test_log_attach.py`).
- **Only paths under `API_V1_PREFIX` are observable.** The box answers on a public IP, and scanner traffic (`/announce`, `/scrape`, `.env` probes) was 93% of BOTH pipelines, dragging the aggregate 4xx rate to ~93%. Two filters, deliberately at different layers: `_ServedPathSampler` drops root spans whose `url.path` is outside the prefix (`otel.py`; `ParentBased` takes the `http send` children with it), and `capture_http_request` drops `path == _UNMATCHED_PATH` (`observability.py`). A route mounted OUTSIDE `/api/v1` gets NO spans — and the sampler decides at span START, so it can only see the path, never the matched route.
- **SQLAlchemy + httpx are instrumented GLOBALLY (`app/main.py`), which is ruinous in a bulk loop.** Right inside a request; but the mfapi sweep touches ~8k schemes, so one run would emit ~25k spans into a trace nothing can render. Jobs wrap their per-item loop in `suppress_instrumentation()` and keep only run/phase spans (`job_tracing.py`). Any new bulk loop must do the same.
- **Background jobs must report failures explicitly.** `capture_exception` is wired only into the HTTP handler (`exceptions.py:96`), and the schedulers CATCH their own exceptions, so `@traced_job`'s span never sees them — without a `report_job_failure` call in the `except`, a crashed job stays green in the trace and files no issue. Reporting is deduped via a flag on the exception, so run+phase nesting files once while both spans still go red (`job_tracing.py:_REPORTED_FLAG`).
- **`DEPLOY_ENV` and `GIT_COMMIT` are set in `ecosystem.config.cjs`, not `.env`.** `config.py` calls `load_dotenv()` without `override`, so the process env wins; that file is the one place that is by definition production. Until 2026-07-30 prod was stamped `environment="development"` / `service_version="unknown"`, making it indistinguishable from a laptop in PostHog. Applied by `pm2 reload --update-env`.
- **The CAS scope is set in `get_current_user` (and re-set in `get_effective_user`).** That one indexed lookup per request is what lets ~56 query sites stay unedited. Code running OUTSIDE a request — schedulers, `BackgroundTasks` callbacks — carries no scope and will read across every statement a user has uploaded unless it wraps itself in `cas_scope_for_user` / resolves `effective_scope` first (`portfolio/services/networth_history_service.py`, `identity/services/onboarding_generation_service.py` both do).
- **`resolve_active_cas_upload_id` probes inside a SAVEPOINT the first time it sees an engine**, and memoises the answer per engine. A missing `cas_uploads` (pre-DDL deploy window, or a test harness that creates only its own tables) then degrades to "no scope" instead of aborting the caller's transaction.
- **`get_current_user` takes `request: Request`** to set `request.state.distinct_id`. Dropping it still imports cleanly — `from __future__ import annotations` defers the annotation — and fails only at request time (`test_exception_identity.py`).

## Typical authenticated call

1. Client sends request with `Authorization: Bearer <jwt>`.
2. FastAPI resolves `get_current_user` — decodes JWT, loads `User`.
3. Optional `X-Family-Member-Id` header triggers `get_effective_user` — swaps to the family-member `User`.
4. Handler receives the effective user and an `AsyncSession` from `get_db()`.
5. Business logic runs; response serialised via a Pydantic schema.
6. On the way out, `otel_response_hook` emits the `http_request` event (route template, status, duration) and the OTLP span is exported.

## Don't read

- `__pycache__/`, `tests/`.
