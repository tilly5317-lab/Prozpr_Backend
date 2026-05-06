"""Verify the long-horizon dummy MF state.

For each profile, prints:
- Number of distinct daily total_value values in last 60 days (must be > 1, proving fluctuation)
- Sample of three (transaction_date, nav) rows compared with the real
  mf_nav_history NAV on those dates (must match exactly).
- Min/max history dates and value range.
"""
from __future__ import annotations
import asyncio
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import text
from app.database import _get_session_factory, dispose_engine

MOBILES = [f"77700000{i:02d}" for i in range(1, 11)]


async def _run() -> None:
    factory = _get_session_factory()
    async with factory() as db:
        for mob in MOBILES:
            uid = (await db.execute(text(
                "SELECT id FROM users WHERE mobile=:m"), {"m": mob})).scalar()

            hist = (await db.execute(text(
                "SELECT MIN(ph.recorded_date), MAX(ph.recorded_date), COUNT(*), "
                "  COUNT(DISTINCT ph.total_value), MIN(ph.total_value), MAX(ph.total_value) "
                "FROM portfolio_history ph JOIN portfolios p ON p.id=ph.portfolio_id "
                "WHERE p.user_id=:u"
            ), {"u": uid})).first()
            mind, maxd, cnt, ndistinct, vmin, vmax = hist

            recent_distinct = (await db.execute(text(
                "SELECT COUNT(DISTINCT ph.total_value) FROM portfolio_history ph "
                "JOIN portfolios p ON p.id=ph.portfolio_id "
                "WHERE p.user_id=:u AND ph.recorded_date >= :cutoff"
            ), {"u": uid, "cutoff": date(2026, 3, 5)})).scalar()

            # Sample: pick 3 BUY rows and verify NAV matches mf_nav_history
            samples = (await db.execute(text(
                "SELECT scheme_code, transaction_date, nav FROM mf_transactions "
                "WHERE user_id=:u ORDER BY transaction_date LIMIT 1"), {"u": uid})).all()
            mid = (await db.execute(text(
                "SELECT scheme_code, transaction_date, nav FROM mf_transactions "
                "WHERE user_id=:u ORDER BY transaction_date OFFSET 100 LIMIT 1"), {"u": uid})).all()
            late = (await db.execute(text(
                "SELECT scheme_code, transaction_date, nav FROM mf_transactions "
                "WHERE user_id=:u ORDER BY transaction_date DESC LIMIT 1"), {"u": uid})).all()

            real_match = 0
            checked = 0
            for r in samples + mid + late:
                checked += 1
                sc, td, nav = r[0], r[1], float(r[2])
                real = (await db.execute(text(
                    "SELECT nav FROM mf_nav_history WHERE scheme_code=:sc AND nav_date<=:d "
                    "ORDER BY nav_date DESC LIMIT 1"
                ), {"sc": sc, "d": td})).scalar()
                if real is not None and abs(float(real) - nav) < 0.01:
                    real_match += 1

            ntx = (await db.execute(text(
                "SELECT COUNT(*) FROM mf_transactions WHERE user_id=:u"), {"u": uid})).scalar()
            print(f"  {mob}: txns={ntx:>4}  hist={cnt}d ({mind}->{maxd})  "
                  f"distinct_vals={ndistinct}  last60d_distinct={recent_distinct}  "
                  f"range=[{float(vmin or 0):,.0f} .. {float(vmax or 0):,.0f}]  "
                  f"nav_match={real_match}/{checked}")
    await dispose_engine()


if __name__ == "__main__":
    asyncio.run(_run())
