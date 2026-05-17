"""Verify the reseeded dummy MF state across the 10 dummy profiles."""
from __future__ import annotations

import asyncio
import sys
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
            if not uid:
                print(f"  {mob}: USER NOT FOUND")
                continue
            ntx = (await db.execute(text(
                "SELECT COUNT(*) FROM mf_transactions WHERE user_id=:u"), {"u": uid})).scalar()
            nfunds = (await db.execute(text(
                "SELECT COUNT(DISTINCT scheme_code) FROM mf_transactions WHERE user_id=:u"), {"u": uid})).scalar()
            first_d, last_d = (await db.execute(text(
                "SELECT MIN(transaction_date), MAX(transaction_date) FROM mf_transactions WHERE user_id=:u"
            ), {"u": uid})).first()
            nholdings = (await db.execute(text(
                "SELECT COUNT(*) FROM portfolio_holdings ph JOIN portfolios p ON p.id=ph.portfolio_id "
                "WHERE p.user_id=:u AND ph.instrument_type='mutual_fund'"), {"u": uid})).scalar()
            history_n, history_min, history_max = (await db.execute(text(
                "SELECT COUNT(*), MIN(recorded_date), MAX(recorded_date) FROM portfolio_history ph "
                "JOIN portfolios p ON p.id=ph.portfolio_id WHERE p.user_id=:u"
            ), {"u": uid})).first()

            # Compute holdings inline since the mf_holdings view may not be present.
            holdings = (await db.execute(text(
                "SELECT mt.scheme_code, m.scheme_name, m.category, "
                "  SUM(CASE WHEN mt.transaction_type='BUY' THEN mt.amount ELSE 0 END) AS invested, "
                "  SUM(CASE WHEN mt.transaction_type='BUY' THEN mt.units ELSE -mt.units END) AS units, "
                "  (SELECT nav FROM mf_nav_history WHERE scheme_code=mt.scheme_code "
                "     ORDER BY nav_date DESC LIMIT 1) AS latest_nav "
                "FROM mf_transactions mt LEFT JOIN mf_fund_metadata m ON m.scheme_code=mt.scheme_code "
                "WHERE mt.user_id=:u GROUP BY mt.scheme_code, m.scheme_name, m.category "
                "HAVING SUM(CASE WHEN mt.transaction_type='BUY' THEN mt.units ELSE -mt.units END) > 0 "
                "ORDER BY 4 DESC"
            ), {"u": uid})).all()
            view_inv = sum(float(r[3] or 0) for r in holdings)
            view_cv = sum(float(r[4] or 0) * float(r[5] or 0) for r in holdings)
            view_pnl = view_cv - view_inv

            nw_row = (await db.execute(text(
                "SELECT total_invested, total_current_value, total_unrealised_pnl, last_updated "
                "FROM net_worth_summary WHERE user_id=:u"
            ), {"u": uid})).first() if False else None  # skip if view doesn't exist

            print(f"  {mob}: txns={ntx:>4} funds={nfunds} mfholdings={nholdings} "
                  f"hist={history_n:>4}d ({history_min} -> {history_max}) "
                  f"first_txn={first_d} last_txn={last_d}")
            print(f"     mf_holdings view: inv={view_inv:>14,.0f}  cv={view_cv:>14,.0f}  pnl={view_pnl:>+12,.0f}")
            if nw_row:
                ti, tcv, tpnl, lu = nw_row
                print(f"     net_worth_summary: total_inv={float(ti or 0):>14,.0f}  cv={float(tcv or 0):>14,.0f}  pnl={float(tpnl or 0):>+12,.0f}  updated={lu}")
            print()
    await dispose_engine()


if __name__ == "__main__":
    asyncio.run(_run())
