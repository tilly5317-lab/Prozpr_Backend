"""Create (or print) the ``vr`` schema.

    python -m scripts.vr_bootstrap --print-sql            # emit DDL, touch nothing
    python -m scripts.vr_bootstrap --write-sql            # refresh migrations/sql/
    python -m scripts.vr_bootstrap --apply                # execute against DATABASE_URL
    python -m scripts.vr_bootstrap --apply --dry-run      # show what --apply would run

Every statement is ``IF NOT EXISTS``, so ``--apply`` is safe to re-run and
cannot alter or drop anything that already exists — including anything in
``public``, which this script never names.

**Why a script and not Alembic:** this repo's Alembic is stamped at a lost
revision (see ``app/core/database.py``), so ``alembic upgrade`` is not a working
path here. This follows the existing convention instead: reviewed SQL under
``migrations/sql/``, applied deliberately.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


def _fail_on_wrong_interpreter(exc: ModuleNotFoundError) -> None:
    """Explain the venv, rather than leaving a bare ModuleNotFoundError.

    On the deploy box ``python3`` is the system interpreter and has none of the
    app's dependencies — PM2 runs ``venv/bin/uvicorn`` (see
    ``ecosystem.config.cjs``), so ops commands need ``venv/bin/python`` too.
    Hitting this as a raw traceback reads like a broken script rather than the
    wrong interpreter, which costs a confusing few minutes every time.
    """
    for candidate in ("venv/bin/python", ".venv/bin/python", ".venv/Scripts/python.exe"):
        if (BACKEND_ROOT / candidate).exists():
            sys.exit(
                f"{exc.name!r} is not installed for {sys.executable}.\n"
                f"This is the app's interpreter, not the system one - re-run as:\n\n"
                f"    {candidate} -m scripts.vr_bootstrap {' '.join(sys.argv[1:])}\n"
            )
    sys.exit(
        f"{exc.name!r} is not installed for {sys.executable}, and no virtualenv "
        f"was found under {BACKEND_ROOT}. Activate the app's environment first."
    )


try:
    from app.domains.vr_data.schema import (  # noqa: E402
        CONTROL_TABLES,
        MIRROR_TABLES,
        create_schema_sql,
    )
    from app.domains.vr_data.specs import all_specs  # noqa: E402
except ModuleNotFoundError as exc:  # pragma: no cover - interpreter guard
    _fail_on_wrong_interpreter(exc)

SQL_PATH = BACKEND_ROOT / "migrations" / "sql" / "vr_schema.sql"


async def apply(dry_run: bool) -> int:
    from sqlalchemy import text

    from app.core.config import get_settings
    from app.core.database import _get_engine

    url = get_settings().get_database_url()
    if not url.startswith("postgresql"):
        print(f"refusing to apply: DATABASE_URL is not postgres ({url.split('://')[0]})")
        return 2

    statements = [s.strip() for s in create_schema_sql().split(";\n") if s.strip()]
    statements = [s for s in statements if not s.startswith("--")]
    if dry_run:
        print(f"-- would execute {len(statements)} statement(s) against {url.split('@')[-1]}")
        for stmt in statements:
            print(stmt.splitlines()[0][:110] + " ...;")
        return 0

    engine = _get_engine()
    async with engine.begin() as conn:
        for stmt in statements:
            await conn.execute(text(stmt))
    print(f"applied {len(statements)} statement(s); vr schema is ready.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--print-sql", action="store_true")
    parser.add_argument("--write-sql", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--summary", action="store_true")
    args = parser.parse_args()

    if args.summary or not any(
        (args.print_sql, args.write_sql, args.apply)
    ):
        specs = all_specs()
        by_tier: dict[str, list[str]] = {}
        for s in specs.values():
            by_tier.setdefault(s.tier, []).append(s.name)
        print(
            f"{len(specs)} declared tables, {len(MIRROR_TABLES)} mirror + "
            f"{len(CONTROL_TABLES)} control tables in schema 'vr'"
        )
        for tier in ("core", "additional", "optional", "support", "candidate"):
            names = sorted(by_tier.get(tier, []))
            if names:
                print(f"  {tier:<10} {len(names):>2}  {', '.join(names)}")
        total_cols = sum(len(s.columns) for s in specs.values())
        print(f"  {total_cols} mirrored columns total")
        if not any((args.print_sql, args.write_sql, args.apply)):
            return 0

    if args.print_sql:
        print(create_schema_sql())

    if args.write_sql:
        SQL_PATH.parent.mkdir(parents=True, exist_ok=True)
        SQL_PATH.write_text(create_schema_sql(), encoding="utf-8")
        print(f"wrote {SQL_PATH.relative_to(BACKEND_ROOT)}")

    if args.apply:
        return asyncio.run(apply(args.dry_run))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
