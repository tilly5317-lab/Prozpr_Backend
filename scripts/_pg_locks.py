"""Print current activity + locks on mf_nav_history / mf_fund_metadata."""
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
                "SELECT pid, state, wait_event_type, wait_event, "
                "  age(clock_timestamp(), query_start) AS qage, "
                "  LEFT(query, 120) AS q "
                "FROM pg_stat_activity "
                "WHERE state IS NOT NULL AND pid <> pg_backend_pid() "
                "ORDER BY qage DESC NULLS LAST"
            )
        )
        for r in rows.all():
            print(r)


if __name__ == "__main__":
    asyncio.run(_run())
