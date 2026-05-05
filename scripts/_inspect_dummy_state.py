"""One-shot: inspect existing dummy users (7770000001-7770000010) and pickable MF universe."""
from __future__ import annotations

import asyncio
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text
from app.database import _get_session_factory, dispose_engine


async def _run() -> None:
    factory = _get_session_factory()
    async with factory() as db:
        rows = (await db.execute(text(
            "SELECT mobile, first_name, last_name, id::text FROM users "
            "WHERE mobile BETWEEN '7770000001' AND '7770000010' ORDER BY mobile"
        ))).all()
        print(f"=== {len(rows)} dummy users found ===")
        for r in rows:
            print(f"  {r[0]} {r[1]} {r[2]} -> {r[3]}")

        for mob in [r[0] for r in rows]:
            uid = (await db.execute(text(
                "SELECT id FROM users WHERE mobile=:m"), {"m": mob}
            )).scalar()
            ntx = (await db.execute(text(
                "SELECT COUNT(*) FROM mf_transactions WHERE user_id=:u"), {"u": uid}
            )).scalar()
            nholdings = (await db.execute(text(
                "SELECT COUNT(*) FROM portfolio_holdings ph JOIN portfolios p ON p.id=ph.portfolio_id "
                "WHERE p.user_id=:u AND ph.instrument_type='mutual_fund'"), {"u": uid}
            )).scalar()
            print(f"  {mob}: {ntx} mf_txns, {nholdings} mf holdings")

        cutoff_2y = date.today() - timedelta(days=730)
        cnt_eq_2y = (await db.execute(text(
            "SELECT COUNT(DISTINCT m.scheme_code) FROM mf_fund_metadata m "
            "WHERE m.is_active=true AND m.category='Equity' AND EXISTS ("
            "  SELECT 1 FROM mf_nav_history h WHERE h.scheme_code=m.scheme_code AND h.nav_date <= :cut"
            ") AND EXISTS ("
            "  SELECT 1 FROM mf_nav_history h WHERE h.scheme_code=m.scheme_code AND h.nav_date >= :recent"
            ")"
        ), {"cut": cutoff_2y, "recent": date.today() - timedelta(days=14)})).scalar() or 0
        cnt_debt_2y = (await db.execute(text(
            "SELECT COUNT(DISTINCT m.scheme_code) FROM mf_fund_metadata m "
            "WHERE m.is_active=true AND m.category='Debt' AND EXISTS ("
            "  SELECT 1 FROM mf_nav_history h WHERE h.scheme_code=m.scheme_code AND h.nav_date <= :cut"
            ") AND EXISTS ("
            "  SELECT 1 FROM mf_nav_history h WHERE h.scheme_code=m.scheme_code AND h.nav_date >= :recent"
            ")"
        ), {"cut": cutoff_2y, "recent": date.today() - timedelta(days=14)})).scalar() or 0
        cnt_hyb_2y = (await db.execute(text(
            "SELECT COUNT(DISTINCT m.scheme_code) FROM mf_fund_metadata m "
            "WHERE m.is_active=true AND m.category='Hybrid' AND EXISTS ("
            "  SELECT 1 FROM mf_nav_history h WHERE h.scheme_code=m.scheme_code AND h.nav_date <= :cut"
            ") AND EXISTS ("
            "  SELECT 1 FROM mf_nav_history h WHERE h.scheme_code=m.scheme_code AND h.nav_date >= :recent"
            ")"
        ), {"cut": cutoff_2y, "recent": date.today() - timedelta(days=14)})).scalar() or 0
        print(f"\nFunds with 2y NAV history (active, recent NAV): "
              f"Equity={cnt_eq_2y}  Debt={cnt_debt_2y}  Hybrid={cnt_hyb_2y}")

        cats = (await db.execute(text(
            "SELECT category, COUNT(*) FROM mf_fund_metadata GROUP BY category ORDER BY 2 DESC"
        ))).all()
        print("\nCategory breakdown:")
        for c, n in cats:
            print(f"  {c}: {n}")

        sample = (await db.execute(text(
            "SELECT m.scheme_code, m.scheme_name, m.amc_name, m.category, m.sub_category, m.plan_type, m.option_type "
            "FROM mf_fund_metadata m WHERE m.is_active=true AND m.category='Equity' "
            "AND m.plan_type='REGULAR' AND m.option_type='GROWTH' "
            "AND EXISTS (SELECT 1 FROM mf_nav_history h WHERE h.scheme_code=m.scheme_code AND h.nav_date <= :cut) "
            "AND EXISTS (SELECT 1 FROM mf_nav_history h WHERE h.scheme_code=m.scheme_code AND h.nav_date >= :recent) "
            "ORDER BY m.scheme_code LIMIT 10"
        ), {"cut": cutoff_2y, "recent": date.today() - timedelta(days=14)})).all()
        print("\nSample equity funds:")
        for s in sample:
            print(f"  {s[0]:8} {s[3]:7} | {s[2][:25]:25} | {s[1][:60]}")

    await dispose_engine()


if __name__ == "__main__":
    asyncio.run(_run())
