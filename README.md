# Ask Tilly — Backend

FastAPI API on PostgreSQL (SQLAlchemy async). AI behaviour is delegated to the bundled
`AI_Agents` package via `sys.path` injection; integration lives in `app/services/ai_bridge/`.
Main app code is under `app/` (routers, services, models, schemas).

## Quick start

From this directory (`Prozpr_Backend/`):

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Set up env vars — copy .env.example to .env and fill in:
#    DATABASE_URL, JWT_SECRET, ENCRYPTION_KEY, ALLOWED_ORIGINS,
#    ANTHROPIC_API_KEY (+ optional per-feature keys)
cp .env.example .env

# 3. Apply migrations
alembic upgrade head

# 4. (Optional) Seed dev data — DESTRUCTIVE
python scripts/reset_and_seed_dummy_data.py

# 5. Run
uvicorn main:app --reload
```

Interactive API docs at `http://localhost:8000/docs` or `/redoc` when the server is running.

## High-level map

- `app/` — FastAPI application (routers, services, models, schemas).
- `AI_Agents/src/` — Agent pipelines; integrated via `sys.path` injection.
- `alembic/` — Database migrations.
- `wealth_core/` — Legacy; see `wealth_core/CLAUDE.md`.
- `MF_Logics/` — Legacy; see `MF_Logics/CLAUDE.md`.
- `scripts/` — Dev-only helper scripts.
- `deploy/` — Deployment artifacts.

## Where to find detail

- **Full tree, subsystem maps, file-level roles:** `CLAUDE.md` files, starting at
  `Prozpr_Backend/CLAUDE.md`.
- **Column-level database schema:** `README_DATABASE_SCHEMA.md`.
- **Live API reference:** `/docs`, `/redoc` when the server is running.
- **Context-layer maintenance:** `/refresh-context` slash command works from any folder.

## Related

- [README_DATABASE_SCHEMA.md](./README_DATABASE_SCHEMA.md) — tables and columns.
- [`docs/superpowers/specs/2026-04-22-backend-context-layer-design.md`](./docs/superpowers/specs/2026-04-22-backend-context-layer-design.md) — design for the CLAUDE.md context layer.
