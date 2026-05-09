"""Inspect NAV date ranges so we can pick a realistic transaction window."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text
from app.database import _get_session_factory, dispose_engine


async def _run() -> None:
    factory = _get_session_factory()
    async with factory() as db:
        mn, mx = (await db.execute(text(
            "SELECT MIN(nav_date), MAX(nav_date) FROM mf_nav_history"
        ))).one()
        print(f"NAV history date range: {mn} -> {mx}")

        rows = (await db.execute(text(
            "SELECT category, COUNT(*) FROM mf_fund_metadata "
            "WHERE category ILIKE ANY(ARRAY['Equity%','Debt%','Hybrid%','Other%','Income%']) "
            "GROUP BY category ORDER BY 2 DESC"
        ))).all()
        print("\nKey categories:")
        for c, n in rows:
            print(f"  {c}: {n}")

        rows = (await db.execute(text(
            "SELECT m.category, COUNT(DISTINCT m.scheme_code) "
            "FROM mf_fund_metadata m WHERE m.is_active=true "
            "AND EXISTS (SELECT 1 FROM mf_nav_history h WHERE h.scheme_code=m.scheme_code AND h.nav_date <= :two_y_ago) "
            "AND EXISTS (SELECT 1 FROM mf_nav_history h WHERE h.scheme_code=m.scheme_code AND h.nav_date >= :recent) "
            "GROUP BY m.category ORDER BY 2 DESC LIMIT 10"
        ), {"two_y_ago": mx and (mx.replace(year=mx.year-2)), "recent": mx})).all() if mx else []
        print("\nFunds with 2y NAV history span (using db max date as 'today'):")
        for c, n in rows:
            print(f"  {c}: {n}")

        rows = (await db.execute(text(
            "SELECT m.scheme_code, m.scheme_name, m.amc_name, m.category, m.sub_category, m.plan_type, m.option_type, "
            "  (SELECT MIN(nav_date) FROM mf_nav_history WHERE scheme_code=m.scheme_code), "
            "  (SELECT MAX(nav_date) FROM mf_nav_history WHERE scheme_code=m.scheme_code), "
            "  (SELECT COUNT(*) FROM mf_nav_history WHERE scheme_code=m.scheme_code) "
            "FROM mf_fund_metadata m WHERE m.is_active=true "
            "AND m.category ILIKE 'Equity%' AND m.plan_type='REGULAR' AND m.option_type='GROWTH' "
            "ORDER BY m.scheme_code LIMIT 10"
        ))).all()
        print("\nSample equity funds with NAV ranges:")
        for s in rows:
            print(f"  {s[0]:>8} | {s[3]:14} | {s[2][:25]:25} | {s[1][:50]:50} | navs={s[9]:5} {s[7]}->{s[8]}")

    await dispose_engine()


if __name__ == "__main__":
    asyncio.run(_run())
