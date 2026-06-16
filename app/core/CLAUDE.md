# app/core/ — Cross-cutting infrastructure

No business logic — only the infra every domain depends on.

## Files

- `config.py` — `get_settings()`: DB URL, `JWT_SECRET`, `ENCRYPTION_KEY`, CORS origins, per-feature Anthropic keys with `ANTHROPIC_API_KEY` as shared fallback.
- `database.py` — `Base` (`DeclarativeBase`), async engine + session factory, `get_db()` dependency, `create_all_tables`, `dispose_engine`, `apply_postgres_schema_patches`.
- `dependencies.py` — `get_current_user` JWT auth; `get_effective_user` family-member resolver (reads `X-Family-Member-Id` header); `get_ai_user_context` User-with-relations loader for AI handlers.
- `security.py` — password hashing (`bcrypt`) + JWT encode/decode.
- `lifespan.py` — FastAPI startup/shutdown; `_start_schedulers()` starts/stops background schedulers, each gated by its own env flag (`MFAPI_SCHEDULER_ENABLED`, `INDEX_TRI_SCHEDULER_ENABLED`).
- `exceptions.py` — centralised exception handlers via `register_exception_handlers(app)` (called once from `app/main.py`). Maps known errors to stable JSON: `ValidationError` → 422; DB auth / host-unreachable / connection-closed → 503; else → 500.

## Typical authenticated call

1. Client sends request with `Authorization: Bearer <jwt>`.
2. FastAPI resolves `get_current_user` — decodes JWT, loads `User`.
3. Optional `X-Family-Member-Id` header triggers `get_effective_user` — swaps to the family-member `User`.
4. Handler receives the effective user and an `AsyncSession` from `get_db()`.
5. Business logic runs; response serialised via a Pydantic schema.

## Don't read

- `__pycache__/`.
