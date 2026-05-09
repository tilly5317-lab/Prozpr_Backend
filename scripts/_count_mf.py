"""One-shot: print current row counts in mf_fund_metadata + mf_nav_history."""
from __future__ import annotations

import asyncio
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text
from app.database import _get_session_factory


async def _run() -> None:
    factory = _get_session_factory()
    async with factory() as db:
        meta = (await db.execute(text("SELECT COUNT(*) FROM mf_fund_metadata"))).scalar() or 0
        nav = (await db.execute(text("SELECT COUNT(*) FROM mf_nav_history"))).scalar() or 0
        cutoff = (date.today() - timedelta(days=30))
        recent = (
            await db.execute(
                text(
                    "SELECT COUNT(DISTINCT scheme_code) FROM mf_nav_history "
                    "WHERE nav_date >= :cutoff"
                ),
                {"cutoff": cutoff},
            )
        ).scalar() or 0
        active_visible = (
            await db.execute(
                text(
                    "SELECT COUNT(*) FROM mf_fund_metadata m WHERE EXISTS ("
                    "  SELECT 1 FROM mf_nav_history h WHERE h.scheme_code=m.scheme_code "
                    "  AND h.nav_date >= :cutoff)"
                ),
                {"cutoff": cutoff},
            )
        ).scalar() or 0
    print(f"mf_fund_metadata rows: {meta}")
    print(f"mf_nav_history rows:   {nav}")
    print(f"distinct schemes with NAV in last 30d: {recent}")
    print(f"metadata rows visible via _has_recent_nav() filter: {active_visible}")


if __name__ == "__main__":
    asyncio.run(_run())
