"""Sanity check: NAV growth for the funds we picked over the last 24 months."""
from __future__ import annotations
import asyncio, sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import text
from app.database import _get_session_factory, dispose_engine

CODES = ["102000", "108466", "107578", "106235", "101762", "103166", "100669",
         "101161", "105758", "114564", "101065", "103819", "100349", "100033",
         "113178", "100177", "105989", "102594", "101979", "112323", "100175",
         "100822", "102948", "102885", "100119", "104685", "113070", "111987",
         "103178", "113047", "100299", "100868"]

TODAY = date(2026, 5, 4)
EARLIEST = TODAY - timedelta(days=730)


async def _run() -> None:
    factory = _get_session_factory()
    async with factory() as db:
        for sc in CODES:
            row = (await db.execute(text(
                "SELECT m.scheme_name, "
                "  (SELECT nav FROM mf_nav_history WHERE scheme_code=:sc AND nav_date <= :start ORDER BY nav_date DESC LIMIT 1) AS nav_start, "
                "  (SELECT nav FROM mf_nav_history WHERE scheme_code=:sc AND nav_date <= :end ORDER BY nav_date DESC LIMIT 1) AS nav_end, "
                "  (SELECT nav FROM mf_nav_history WHERE scheme_code=:sc AND nav_date <= :one_y ORDER BY nav_date DESC LIMIT 1) AS nav_1y "
                "FROM mf_fund_metadata m WHERE m.scheme_code=:sc"
            ), {"sc": sc, "start": EARLIEST, "end": TODAY, "one_y": TODAY - timedelta(days=365)})).first()
            if row:
                ns, ne, n1 = row[1], row[2], row[3]
                if ns and ne:
                    grow_2y = (float(ne) / float(ns) - 1) * 100
                    grow_1y = (float(ne) / float(n1) - 1) * 100 if n1 else 0
                    print(f"  {sc:>8} {(row[0] or '')[:50]:50}  2y={grow_2y:+6.1f}%  1y={grow_1y:+6.1f}%  ({ns} -> {ne})")
                else:
                    print(f"  {sc:>8} {row[0]} - missing nav")
    await dispose_engine()


if __name__ == "__main__":
    asyncio.run(_run())
