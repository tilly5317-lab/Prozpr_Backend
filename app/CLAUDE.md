# app/ — FastAPI application (domain-first)

FastAPI application package. The codebase is organised **domain-first**: every
business capability owns its own `models/` + `schemas/` + `routers/` +
`services/` under `app/domains/<family>/`. Cross-cutting infrastructure
(config, DB, deps, security, exception handlers) lives in `app/core/`.

AI workloads are delegated to `AI_Agents/` via `sys.path` injection.

## Child modules

- **core/** — cross-cutting infra: `config.py`, `database.py`,
  `dependencies.py`, `security.py`, `exceptions.py`. No domain logic here.
- **domains/** — one folder per business domain. Each has the same four
  sub-folders so the shape is predictable. Currently:
  - **identity/** — user, auth, OTP, family members, linked accounts, onboarding
  - **profile/** — risk, tax, investment, constraints, personal finance, properties
  - **goals/** — financial goals, contributions, holdings
  - **portfolio/** — portfolio + allocations + holdings + history + NAV history
  - **mutual_funds/** — MF metadata, NAV, txns, SIPs, snapshots, watchlists, AA imports, mfapi.in
  - **equities/** — company metadata, prices, transactions
  - **asset_allocation/** — allocation runs, buckets, aggregates, targets
  - **rebalancing/** — rebalancing runs, trades, warnings, fund rows, subgroup summaries
  - **cashflow/** — cashflow plan engine: assumptions, one-off events, plan runs, headlines
  - **ingestion/** — CAMS-CAS PDF, SimBanks, Finvu (legacy); all ingest adapters
  - **advisory/** — IPS, meeting notes, discovery helpers
  - **notifications/** — notification records and delivery
  - **chat/** — chat sessions, messages, per-session state, AI module run telemetry
  - **ai_engine/** — chat orchestrator (`ChatBrain`), intent router (the
    `_DISPATCH` switch), per-intent bridges to `AI_Agents`, answer formatter,
    visualizations. The brain entry point for every chat turn lives here.
- **routers/** — only `health.py` and `tags.py` (OpenAPI tag metadata) remain.
  The aggregator `__init__.py` imports each domain's routers and exposes them
  as `all_routers` for `main.py` to mount under `/api/v1`.

## Files at this level

- `main.py` — App factory, CORS, lifespan (metadata `create_all`, engine
  dispose), mounts `all_routers` at `API_V1_PREFIX`, exception handlers.
- `all_models.py` — Imports every domain's ORM models so they register with
  `Base.metadata` (used by Alembic's `env.py` for migration coverage).
- `data/dummy_data.json` + `data/mf_tables_sample.json` — dev seed fixtures.

## Conventions

- **Layer shape per domain.** Every domain has all four sub-folders even when
  sparse. Router files end in `_router.py`; service files end in
  `_service.py` (helpers like `paging.py` keep their own name).
- **Router aggregator.** `app/routers/__init__.py` is the single place that
  knows the order routers mount. `main.py` only imports `all_routers`.
- **Auth.** JWT via `app.core.dependencies.get_current_user`; family-member
  override via `X-Family-Member-Id` header resolved by
  `get_effective_user`. AI handlers receive a pre-loaded User graph via
  `get_ai_user_context`.
- **Async everywhere.** `get_db()` yields an `AsyncSession`; all DB calls
  use `await session.execute(...)`.
- **LLM calls go through LangChain.** All Claude calls must use
  `langchain-anthropic` (`ChatAnthropic` directly or via LCEL chains). Do not
  import `anthropic` for `messages.create`. The only permitted raw
  `anthropic` imports are exception classes (e.g.
  `from anthropic import AuthenticationError`) for `except` clauses.

## Flows

Cross-cutting flows live with their home folders:

- Chat turn (`ChatBrain.run_turn`) → `app/domains/ai_engine/CLAUDE.md`.
- CAMS CAS PDF ingest, SimBanks sync → `app/domains/ingestion/CLAUDE.md`.

## Don't read

- `__pycache__/`, `.pytest_cache/`, `.venv/`, `.obsidian/` — build/editor caches.
- `*.db`, `*.db.bak-*`, `*.db.partial-*` — local SQLite dev state.
- `market_commentary_*.json`, `market_commentary_*.md` — runtime cache files.

## Refresh

If any CLAUDE.md in this tree looks stale after a structural change, run
`/refresh-context` from that folder. Leaf CLAUDE.mds intentionally omit a
per-file refresh note — this is the canonical one.
