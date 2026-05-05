"""Inspect plan_type/option_type values + name conventions."""
from __future__ import annotations
import asyncio, sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import text
from app.database import _get_session_factory, dispose_engine


async def _run() -> None:
    factory = _get_session_factory()
    async with factory() as db:
        for col in ["plan_type", "option_type"]:
            rows = (await db.execute(text(
                f"SELECT {col}, COUNT(*) FROM mf_fund_metadata GROUP BY {col} ORDER BY 2 DESC"
            ))).all()
            print(f"\n{col}:")
            for v, n in rows:
                print(f"  {v}: {n}")

        # Search for HDFC by name without plan filter
        rows = (await db.execute(text(
            "SELECT scheme_code, scheme_name, amc_name, category, plan_type, option_type "
            "FROM mf_fund_metadata WHERE scheme_name ILIKE '%HDFC%Flexi Cap%' AND is_active=true LIMIT 8"
        ))).all()
        print("\nHDFC Flexi Cap (any plan):")
        for r in rows:
            print(f"  {r[0]:>8} | {r[3]:14} | {r[4]} | {r[5]} | {r[2][:20]:20} | {r[1][:65]}")

        rows = (await db.execute(text(
            "SELECT scheme_code, scheme_name, amc_name, category, plan_type, option_type "
            "FROM mf_fund_metadata WHERE scheme_name ILIKE '%SBI%' AND scheme_name ILIKE '%Bluechip%' AND is_active=true LIMIT 8"
        ))).all()
        print("\nSBI Bluechip (any plan):")
        for r in rows:
            print(f"  {r[0]:>8} | {r[3]:14} | {r[4]} | {r[5]} | {r[2][:20]:20} | {r[1][:65]}")

        # Check funds with many NAVs from real AMCs
        rows = (await db.execute(text(
            "SELECT m.scheme_code, m.scheme_name, m.amc_name, m.category, m.plan_type, m.option_type, "
            "  (SELECT MIN(nav_date) FROM mf_nav_history h WHERE h.scheme_code=m.scheme_code) AS minn, "
            "  (SELECT MAX(nav_date) FROM mf_nav_history h WHERE h.scheme_code=m.scheme_code) AS maxn "
            "FROM mf_fund_metadata m "
            "WHERE m.is_active=true AND m.scheme_name ILIKE '%HDFC%' "
            "  AND EXISTS (SELECT 1 FROM mf_nav_history h WHERE h.scheme_code=m.scheme_code AND h.nav_date<='2024-05-04') "
            "  AND EXISTS (SELECT 1 FROM mf_nav_history h WHERE h.scheme_code=m.scheme_code AND h.nav_date>='2026-04-15') "
            "  AND m.category ILIKE 'Equity%' AND m.scheme_name ILIKE '%Growth%' "
            "ORDER BY m.scheme_code LIMIT 12"
        ))).all()
        print("\nHDFC equity funds with full 2y NAV history:")
        for r in rows:
            print(f"  {r[0]:>8} | {r[3]:14} | {r[4]} | {r[5]} | {r[2][:20]:20} | {r[1][:75]}")
    await dispose_engine()


if __name__ == "__main__":
    asyncio.run(_run())
