# Prozpr_Backend/ — Ask Tilly backend

Ask Tilly is an AI-powered financial advisor. This package is the backend: FastAPI on PostgreSQL (SQLAlchemy async), with AI workloads integrated from a bundled `AI_Agents/` package via `sys.path` injection. For setup and run instructions, see `README.md`. For column-level database schema, see `README_DATABASE_SCHEMA.md`.

## Child modules

- **app/** — FastAPI application (routers, services, models, schemas).
- **AI_Agents/src/** — Agent pipelines (allocation, intent, market commentary, portfolio query, risk profiling, goal-based allocation, drift analysis); integrated via `sys.path` injection.
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

If this file looks stale after a structural change, run `/refresh-context` from this folder.
