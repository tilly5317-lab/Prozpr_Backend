# Prozpr_Backend/ — Ask PI backend

Ask PI is an AI-powered financial advisor. This package is the backend: FastAPI on PostgreSQL (SQLAlchemy async), with AI workloads integrated from a bundled `AI_Agents/` package via `sys.path` injection. For setup and run instructions, see `README.md`. For column-level database schema, see `README_DATABASE_SCHEMA.md`.

## Child modules

- **app/** — FastAPI application (routers, services, models, schemas).
- **AI_Agents/src/** — Agent pipelines (asset_allocation_pydantic, practical_asset_allocation, cashflow_statement, Rebalancing, additional_investment, intent_classifier, market_commentary, portfolio_query, risk_profiling) plus the `financial_primitives/` numeric-kernel library; via `sys.path` injection. See `AI_Agents/src/CLAUDE.md` for the full module map.
- **alembic/** — Database migrations.
- **migrations/** — Hand-written raw SQL migration scripts (under `sql/`) for asset-allocation schema changes; applied manually, distinct from the Alembic-managed `alembic/` migrations.
- **notebooks/** — DEV-ONLY exploration Jupyter notebooks (e.g. a portfolio-vs-Nifty-50 benchmark prototype). Not imported by runtime.
- **wealth_core/** — LEGACY; pre-app/ orchestration modules.
- **MF_Logics/** — LEGACY; historical MF data extraction and mapping work.
- **scripts/** — DEV-ONLY helper scripts.
- **deploy/** — DEPLOY-ONLY; deployment artifacts.
- **AI_Agents/archive/** — ARCHIVED agent implementations.
- **Agent_audit/** — DEV-ONLY audit fixtures: question set (`questions.json`), audit runner (`run_audit.py`), per-finding smoke scripts, and captured transcripts/findings. Not imported by runtime.

## Files at this level

- `main.py` — uvicorn entry point; re-exports `app.main:app` so `uvicorn main:app` boots the FastAPI server.
- `alembic.ini` — Alembic migrations configuration; points at `alembic/env.py` for the migration environment.
- `requirements.txt` — Python runtime dependencies for the backend (pip install target).
- `Dockerfile` — container image definition used to build the deployable backend image.
- `pyrightconfig.json` — Pyright static type-checker configuration for the repo.
- `pyproject.toml` — pytest configuration: `asyncio_mode=auto`, custom markers, and `pythonpath = ["AI_Agents/src", "."]` so agent imports resolve in tests without `ensure_ai_agents_path()`.
- `ruff.toml` — Ruff linter/formatter configuration applied across the project.

## Conventions

- **LLM calls go through LangChain.** All Claude calls must use `langchain-anthropic` (`ChatAnthropic` directly or via LCEL chains). Do not import `anthropic` for `messages.create` — the only permitted raw `anthropic` imports are exception classes (e.g. `from anthropic import AuthenticationError`) for `except` clauses, since those live only in the SDK.
- **Observability is PostHog-only.** OTel (`app/core/otel.py`) exports spans and logs over OTLP; the PostHog Python SDK emits durable events. New Relic was removed on 2026-07-27 — do not reintroduce an APM agent alongside the OTel SDK, they fight over the same instrumentation hooks (`NEW_RELIC_OPENTELEMETRY_ENABLED` hijacks the TracerProvider, silently sending your exporter zero spans).
- **Reference docs refresh manually, not on code changes.** A logic or production-wiring change is complete without updating any `AI_Agents/Reference_docs/` doc — do not rewrite, version-bump, or hunt for drift in them as a side effect. Refresh a reference doc only when a human explicitly asks. (The `Logics_reference_docs/*.md` ground customer-facing chat answers, so their accuracy is owned by whoever triggers the refresh.) CLAUDE.md files are exempt — those may still be kept current.

## Testing

- Run via `.venv-mac/bin/python -m pytest` (config in `pyproject.toml`, `asyncio_mode=auto`). Same prefix for `alembic`.
- sqlite DB tests: `Base.metadata.create_all` FAILS (an unrelated model uses a Postgres `ARRAY`). Create only the table(s) under test: `await conn.run_sync(MyModel.__table__.create)`.
- New ORM model → register in `app/all_models.py` (for `Base.metadata`) AND the domain `models/__init__.py`.

## Flows

Cross-cutting flows live with their home folders:
- Typical authenticated call → `app/core/CLAUDE.md`.
- Chat turn (`ChatBrain.run_turn`) → `app/domains/ai_engine/CLAUDE.md`.
- Allocation (computed in `asset_allocation/services/aa_engine/`, persisted/read by the same domain; `ai_engine` only sequences the call from `flow_asset_allocation`) → `app/domains/asset_allocation/CLAUDE.md`.
- CAMS CAS PDF ingest, SimBanks sync, Finvu (legacy) → `app/domains/ingestion/CLAUDE.md`.

## Don't read

- `__pycache__/`, `.pytest_cache/`, `.venv/`, `.obsidian/` — build/editor caches.
- `*.db`, `*.db.bak-*`, `*.db.partial-*`, `*.db.probe-artifact-*` — local SQLite dev state.
- `market_commentary_*.json`, `market_commentary_*.md` — runtime cache files.
- `docs/` — non-runtime documentation artifacts (`superpowers/` planning scaffolding: `specs/`, `plans/`, `notes/`). Not product code.

## Context-layer convention

Every folder's `CLAUDE.md` follows convention v2 — full rules and the reconcile procedure live in `.claude/commands/refresh-context.md`; run `/refresh-context` from a folder to check it. In brief:

- **Section order:** `# path/ — purpose` → optional `## Entry / contract` → typed structure (`## Child modules` | `## Layers` | `## Files`) → optional `## Gotchas & invariants` → optional `## Testing` → `## Don't read`.
- **Type** is the structure marker; `## Imported by active code?` marks a **Stub** (legacy / not-imported only). Each Gotchas bullet carries a `file:line`/symbol anchor + the *why*.
- **Keep** stable contracts, cross-module invariants, numeric conventions, env flags; **drop** test rosters, symbol lists, "N files" counts, internal helper names.
- **Size by words, not lines:** Stub ≤120 · Leaf ≤250 (≤400 flow-bearing) · Map ≤400 · hub ≤600; one idea per bullet.
