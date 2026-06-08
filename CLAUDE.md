# Prozpr_Backend/ — Ask PI backend

Ask PI is an AI-powered financial advisor. This package is the backend: FastAPI on PostgreSQL (SQLAlchemy async), with AI workloads integrated from a bundled `AI_Agents/` package via `sys.path` injection. For setup and run instructions, see `README.md`. For column-level database schema, see `README_DATABASE_SCHEMA.md`.

## Child modules

- **app/** — FastAPI application (routers, services, models, schemas).
- **AI_Agents/src/** — Agent pipelines (asset_allocation_pydantic, cashflow_statement, Rebalancing, intent_classifier, market_commentary, portfolio_query, risk_profiling) plus the `financial_primitives/` numeric-kernel library; integrated via `sys.path` injection. See `AI_Agents/src/CLAUDE.md` for the full module map.
- **alembic/** — Database migrations.
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
- `ruff.toml` — Ruff linter/formatter configuration applied across the project.

## Conventions

- **LLM calls go through LangChain.** All Claude calls must use `langchain-anthropic` (`ChatAnthropic` directly or via LCEL chains). Do not import `anthropic` for `messages.create` — the only permitted raw `anthropic` imports are exception classes (e.g. `from anthropic import AuthenticationError`) for `except` clauses, since those live only in the SDK.

## Testing

- Run via `.venv-mac/bin/python -m pytest` (config in `pyproject.toml`, `asyncio_mode=auto`). Same prefix for `alembic`.
- sqlite DB tests: `Base.metadata.create_all` FAILS (an unrelated model uses a Postgres `ARRAY`). Create only the table(s) under test: `await conn.run_sync(MyModel.__table__.create)`.
- New ORM model → register in `app/all_models.py` (for `Base.metadata`) AND the domain `models/__init__.py`.

## Flows

Cross-cutting flows live with their home folders:
- Typical authenticated call → `app/CLAUDE.md`.
- Chat turn (`ChatBrain.run_turn`) → `app/domains/ai_engine/CLAUDE.md`.
- Allocation (produced by the AI bridge in `ai_engine`; persisted/read in `asset_allocation`) → `app/domains/asset_allocation/CLAUDE.md`.
- CAMS CAS PDF ingest, SimBanks sync, Finvu (legacy) → `app/domains/ingestion/CLAUDE.md`.

## Don't read

- `__pycache__/`, `.pytest_cache/`, `.venv/`, `.obsidian/` — build/editor caches.
- `*.db`, `*.db.bak-*`, `*.db.partial-*`, `*.db.probe-artifact-*` — local SQLite dev state.
- `market_commentary_*.json`, `market_commentary_*.md` — runtime cache files.
- `docs/` — non-runtime documentation artifacts (`superpowers/` planning scaffolding, `charts.md`, `flowchart_chat_flow.html`). Not product code.

## Refresh

If any CLAUDE.md in this tree looks stale after a structural change, run `/refresh-context` from that folder. (Leaf CLAUDE.mds intentionally omit a per-file refresh note — this is the canonical one.)
