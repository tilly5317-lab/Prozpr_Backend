# alembic/ — Database migrations

Alembic migrations for the PostgreSQL database. `env.py` imports `app.models` so every ORM class registers with `Base.metadata` and gets migration coverage. Revisions live under `versions/`.

## Files

- `env.py` — Alembic environment. Imports `app.models`; configures async engine from `app.config.get_settings()`.
- `script.py.mako` — revision file template.
- `versions/` — migration revisions (11 files, one per revision).

## Entry point

- Apply: `alembic upgrade head`.
- Create revision: `alembic revision --autogenerate -m "<message>"`.
- Config: `alembic.ini` at the repo root.

Prefer migrations in shared/prod environments. `app.database.create_all_tables` helps local dev scenarios only.

## Depends on

- `app/models/*` — full set of ORM classes (loaded via `app/models/__init__.py`).
- `app.config.get_settings` — for `DATABASE_URL`.
- `alembic.ini` at `Prozpr_Backend/alembic.ini`.

## Don't read

- `versions/*.py` — individual revision files; read `alembic history` output for timeline, not the files.
- `__pycache__/`.

## Refresh

If stale, run `/refresh-context` from this folder.
