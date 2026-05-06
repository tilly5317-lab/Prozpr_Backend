"""Terminate Postgres backends stuck 'idle in transaction' for >5 min holding
locks from killed runs. Safe: only targets long-idle-in-tx connections from
this app DB, never an active transaction."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text
from app.database import _get_session_factory


async def _run() -> None:
    factory = _get_session_factory()
    async with factory() as db:
        rows = await db.execute(
            text(
                "SELECT pid, age(clock_timestamp(), query_start) AS qage "
                "FROM pg_stat_activity "
                "WHERE state = 'idle in transaction' "
                "  AND age(clock_timestamp(), query_start) > interval '5 minutes' "
                "  AND pid <> pg_backend_pid()"
            )
        )
        targets = [(r[0], r[1]) for r in rows.all()]
        print(f"orphaned idle-in-tx backends to terminate: {len(targets)}")
        for pid, age in targets:
            print(f"  pid={pid} age={age}")
            await db.execute(text("SELECT pg_terminate_backend(:pid)"), {"pid": pid})
        await db.commit()
        print("done")


if __name__ == "__main__":
    asyncio.run(_run())
