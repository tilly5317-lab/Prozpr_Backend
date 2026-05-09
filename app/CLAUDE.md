# app/ — FastAPI application

FastAPI application package hosting all `/api/v1` HTTP routers, async PostgreSQL access via
SQLAlchemy, and per-request auth/context dependencies. AI workloads are delegated to
`AI_Agents/` via `sys.path` injection; this layer owns only the HTTP surface and ORM wiring.

## Child modules

- **models/** — SQLAlchemy ORM classes; one file/subpackage per domain (profile, goals, mf, stocks, etc.).
- **schemas/** — Pydantic request/response models (not ORM).
- **routers/** — HTTP routers, mounted under `/api/v1`.
- **services/** — business logic; chat orchestration, AI bridges, domain services.

## Files at this level

- `main.py` — App factory, CORS, lifespan (metadata `create_all`, engine dispose), mounts
  `all_routers` at `API_V1_PREFIX`, validation/DB-friendly error handlers.
- `config.py` — `get_settings()`: DB URL, `JWT_SECRET`, `ENCRYPTION_KEY`, CORS origins,
  per-feature Anthropic keys with `ANTHROPIC_API_KEY` as shared fallback.
- `database.py` — `Base`, async engine/session, `get_db()`, `create_all_tables`,
  `dispose_engine`.
- `dependencies.py` — `get_current_user` JWT-auth dependency, `get_effective_user`
  family-member resolver, `get_ai_user_context` User-with-relations loader for AI.
- `utils/security.py` — password hashing and JWT encode/decode (inline here; `utils/` has
  only this one file).
- `data/dummy_data.json` — seed fixture for `scripts/reset_and_seed_dummy_data.py` (dev only).
- `data/mf_tables_sample.json` — sample mutual-fund table data for dev/testing (dev only).

## Conventions

- Router files are mounted under `/api/v1` via `routers/__init__.py`.
- All ORM models import into `models/__init__.py` so `Base.metadata` and Alembic see every
  table.
- Authentication: JWT via `dependencies.get_current_user`, family-member override via
  `X-Family-Member-Id` header resolved by `get_effective_user`.
- AI bridges live under `services/ai_bridge/`; they map ORM data to `AI_Agents` DTOs and
  format AI replies for chat.
- Async everywhere: `get_db()` yields an `AsyncSession`; all DB calls use
  `await session.execute(...)`.

## Flows

**Typical authenticated call**
1. Client sends request with `Authorization: Bearer <jwt>`.
2. FastAPI resolves `get_current_user` dependency — decodes JWT, loads `User`.
3. Optional `X-Family-Member-Id` header triggers `get_effective_user` — swaps to the
   family-member `User`.
4. Handler receives the effective user and a `get_db()` `AsyncSession`.
5. Business logic runs; response serialized via Pydantic schema.

## Don't read

- `__pycache__/` — compiled cache.
- `models/__init__.py` table-import bookkeeping (it's just re-exports).
