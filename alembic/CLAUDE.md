# alembic/ — Database migrations

Alembic migrations for the PostgreSQL database. `env.py` imports `app.all_models` so every ORM class registers with `Base.metadata` and gets autogenerate coverage.

## Entry / contract

- Apply: `alembic upgrade head`.
- Create revision: `alembic revision --autogenerate -m "<message>"`.
- Config: `alembic.ini` at the repo root.

## Files

- `env.py` — Alembic environment. Imports `Base` from `app.core.database` plus `app.all_models`; builds the async engine from `app.core.config.get_settings`.
- `script.py.mako` — revision file template.
- `versions/` — migration revisions.

## Gotchas & invariants

- Prefer migrations in shared/prod environments. `app.core.database.create_all_tables` (`app/core/database.py`) is a local-dev convenience only — it bypasses revision history.

## Don't read

- `versions/*.py` — read `alembic history` for the timeline, not the files.
- `__pycache__/`.
