# Prozpr_Backend/ — Ask Tilly backend

Ask Tilly is an AI-powered financial advisor. This package is the backend: FastAPI on PostgreSQL (SQLAlchemy async), with AI workloads integrated from a bundled `AI_Agents/` package via `sys.path` injection. For setup and run instructions, see `README.md`. For column-level database schema, see `README_DATABASE_SCHEMA.md`.

## Child modules

- **app/** — FastAPI application (routers, services, models, schemas).
- **AI_Agents/src/** — Agent pipelines (asset_allocation_pydantic, Rebalancing, intent_classifier, market_commentary, portfolio_query, risk_profiling); integrated via `sys.path` injection. `router/` is a stub placeholder.
- **alembic/** — Database migrations.
- **wealth_core/** — LEGACY; pre-app/ orchestration modules.
- **MF_Logics/** — LEGACY; historical MF data extraction and mapping work.
- **scripts/** — DEV-ONLY helper scripts.
- **deploy/** — DEPLOY-ONLY; deployment artifacts.
- **AI_Agents/archive/** — ARCHIVED agent implementations.

## Files at this level

- `main.py` — uvicorn entry point; re-exports `app.main:app` so `uvicorn main:app` boots the FastAPI server.
- `alembic.ini` — Alembic migrations configuration; points at `alembic/env.py` for the migration environment.
- `requirements.txt` — Python runtime dependencies for the backend (pip install target).
- `Dockerfile` — container image definition used to build the deployable backend image.
- `pyrightconfig.json` — Pyright static type-checker configuration for the repo.
- `ruff.toml` — Ruff linter/formatter configuration applied across the project.

## Conventions

- **LLM calls go through LangChain.** All Claude calls must use `langchain-anthropic` (`ChatAnthropic` directly or via LCEL chains). Do not import `anthropic` for `messages.create` — the only permitted raw `anthropic` imports are exception classes (e.g. `from anthropic import AuthenticationError`) for `except` clauses, since those live only in the SDK.

## Flows

Cross-cutting flows live with their home folders:
- Typical authenticated call → `app/CLAUDE.md`.
- Chat turn (`ChatBrain.run_turn`) → `app/services/chat_core/CLAUDE.md`.
- Allocation spine → `app/services/ai_bridge/CLAUDE.md`.
- Finvu sync → `app/services/CLAUDE.md`.
- SimBanks → `app/services/CLAUDE.md`.

## Don't read

- `__pycache__/`, `.pytest_cache/`, `.venv/`, `.obsidian/` — build/editor caches.
- `*.db`, `*.db.bak-*`, `*.db.partial-*`, `*.db.probe-artifact-*` — local SQLite dev state.
- `market_commentary_*.json`, `market_commentary_*.md` — runtime cache files.
- `docs/superpowers/` — local planning scaffolding (specs, plans), not product code.

## Refresh

If any CLAUDE.md in this tree looks stale after a structural change, run `/refresh-context` from that folder. (Leaf CLAUDE.mds intentionally omit a per-file refresh note — this is the canonical one.)
