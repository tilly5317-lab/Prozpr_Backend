# app/core/ — Cross-cutting infrastructure

No business logic — only the infra every domain depends on.

## Files

- `config.py` — `get_settings()`: DB URL, `JWT_SECRET`, `ENCRYPTION_KEY`, CORS origins, `DEPLOY_ENV`, per-feature Anthropic keys with `ANTHROPIC_API_KEY` as shared fallback. `_backend_dir` is `parents[2]` (repo root) — it was `parents[1]` and only worked because PM2 sets `cwd` there.
- `database.py` — `Base` (`DeclarativeBase`), async engine + session factory, `get_db()` dependency, `create_all_tables`, `dispose_engine`, `apply_postgres_schema_patches`.
- `dependencies.py` — `get_current_user` JWT auth; `get_effective_user` family-member resolver (reads `X-Family-Member-Id` header); `get_ai_user_context` User-with-relations loader for AI handlers.
- `security.py` — password hashing (`bcrypt`) + JWT encode/decode.
- `lifespan.py` — FastAPI startup/shutdown; `_start_schedulers()` starts/stops background schedulers, each gated by its own env flag (`MFAPI_SCHEDULER_ENABLED`, `BENCHMARK_SCHEDULER_ENABLED` — legacy alias `INDEX_TRI_SCHEDULER_ENABLED` still honoured, `config.py:359`).
- `exceptions.py` — `register_exception_handlers(app)` (called once from `app/main.py`) maps known errors to stable JSON: `ValidationError` → 422 (logs field NAMES only — `str(exc)` embeds submitted values); `ClientDisconnect` → 499; DB auth / host-unreachable / connection-closed → 503; else → 500 reported to PostHog via `capture_exception()`.
- `observability.py` — PostHog client lifecycle, `capture_exception()` for 5xx, and `capture_http_request()` + the two OTel hooks that emit the durable per-request event.
- `otel.py` — OTel bootstrap: tracer + logger providers exporting over OTLP to PostHog (`/i/v1/traces`, `/i/v1/logs`), authenticated with the same `phc_` project token. `attach_otel_logging()` ships stdlib logs at WARNING+; `shutdown_otel()` flushes both.
- `log_scrubber.py` — `LogRecordProcessor` redacting phone/PAN/email from log bodies before export. Defence-in-depth; call sites still must not log PII.

## Gotchas & invariants

- **`FastAPIInstrumentor.instrument_app()` must stay at module scope in `app/main.py`.** Inside the lifespan it is a SILENT no-op — Starlette caches `middleware_stack` on the first `__call__`, which the lifespan scope itself triggers (`test_otel_instrumentation_slot.py`).
- **Spans and events are two pipelines on purpose.** OTLP spans/logs retain ~14 days; events retain 12 months. `http_request` and `$ai_generation` are events *because* they need long history — do not "consolidate" them into spans.
- **`path` is always the route template**, never `request.url.path` — 58 of 222 routes are parameterised, and raw paths put user IDs into analytics properties (`observability.py:capture_http_request`).
- **Request duration comes from `scope[_START_NS_KEY]`, not the hook's span.** `client_response_hook` receives the `http send` CHILD span; timing off it reports ~0.01ms for a 50ms request. `otel_request_hook` stamps the server span's start onto the scope.
- **`attach_otel_logging()` attaches to `""` and `uvicorn`, NOT `uvicorn.error`** — the latter has no `propagate` override in uvicorn's `LOGGING_CONFIG` and bubbles up, so both would export every traceback twice (`test_log_attach.py`).
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
