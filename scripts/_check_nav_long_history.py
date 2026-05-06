"""Check how many funds in our hand-picked universe have NAV history going back 5/7/9 years."""
from __future__ import annotations
import asyncio
import sys
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


async def _run() -> None:
    factory = _get_session_factory()
    async with factory() as db:
        print(f"{'code':>8} {'name':50} {'min_date':10} {'max_date':10} {'cnt':>6} {'9y':>4} {'7y':>4} {'5y':>4}")
        for sc in CODES:
            r = (await db.execute(text(
                "SELECT m.scheme_name, MIN(h.nav_date), MAX(h.nav_date), COUNT(*) "
                "FROM mf_fund_metadata m LEFT JOIN mf_nav_history h ON h.scheme_code=m.scheme_code "
                "WHERE m.scheme_code=:sc GROUP BY m.scheme_name"
            ), {"sc": sc})).first()
            if not r:
                print(f"  {sc:>8} (not found)")
                continue
            name, mnd, mxd, cnt = r
            t9 = TODAY - timedelta(days=9*365)
            t7 = TODAY - timedelta(days=7*365)
            t5 = TODAY - timedelta(days=5*365)
            ok9 = "Y" if mnd and mnd <= t9 else "-"
            ok7 = "Y" if mnd and mnd <= t7 else "-"
            ok5 = "Y" if mnd and mnd <= t5 else "-"
            print(f"  {sc:>8} {(name or '')[:48]:48} {str(mnd):10} {str(mxd):10} {cnt:>6} {ok9:>4} {ok7:>4} {ok5:>4}")
    await dispose_engine()


if __name__ == "__main__":
    asyncio.run(_run())
