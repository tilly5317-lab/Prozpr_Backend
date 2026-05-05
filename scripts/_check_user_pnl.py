"""Per-fund PnL check for a user."""
from __future__ import annotations
import asyncio, sys
from datetime import date
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import text
from app.database import _get_session_factory, dispose_engine

MOB = sys.argv[1] if len(sys.argv) > 1 else "7770000001"


async def _run() -> None:
    factory = _get_session_factory()
    async with factory() as db:
        uid = (await db.execute(text("SELECT id FROM users WHERE mobile=:m"), {"m": MOB})).scalar()
        rows = (await db.execute(text(
            "SELECT mt.scheme_code, m.scheme_name, "
            "  SUM(mt.amount) AS invested, SUM(mt.units) AS units, "
            "  COUNT(*) AS txn_count, MIN(mt.transaction_date) AS first_d, MAX(mt.transaction_date) AS last_d, "
            "  (SELECT nav FROM mf_nav_history WHERE scheme_code=mt.scheme_code ORDER BY nav_date DESC LIMIT 1) AS latest_nav "
            "FROM mf_transactions mt LEFT JOIN mf_fund_metadata m ON m.scheme_code=mt.scheme_code "
            "WHERE mt.user_id=:u GROUP BY mt.scheme_code, m.scheme_name ORDER BY 3 DESC"
        ), {"u": uid})).all()
        total_inv = 0.0
        total_cv = 0.0
        print(f"\n=== {MOB} per-fund breakdown ===")
        for r in rows:
            sc, nm, inv, units, n, fd, ld, latest = r
            cv = float(units) * float(latest)
            pnl = cv - float(inv)
            pnl_pct = pnl / float(inv) * 100
            print(f"  {sc:>8} {(nm or '')[:45]:45} | inv={float(inv):>12,.0f} | cv={cv:>12,.0f} | pnl={pnl:>+10,.0f} ({pnl_pct:+5.1f}%) | {n} txns | {fd}->{ld}")
            total_inv += float(inv); total_cv += cv
        print(f"\n  TOTAL: invested={total_inv:,.0f}  cv={total_cv:,.0f}  pnl={total_cv-total_inv:+,.0f} ({(total_cv-total_inv)/total_inv*100:+.2f}%)")

        # First and last txn for one fund — show NAV at first txn vs latest
        if rows:
            first_sc = rows[0][0]
            first_txn = (await db.execute(text(
                "SELECT transaction_date, nav, units, amount FROM mf_transactions "
                "WHERE user_id=:u AND scheme_code=:sc ORDER BY transaction_date LIMIT 5"
            ), {"u": uid, "sc": first_sc})).all()
            last_txn = (await db.execute(text(
                "SELECT transaction_date, nav, units, amount FROM mf_transactions "
                "WHERE user_id=:u AND scheme_code=:sc ORDER BY transaction_date DESC LIMIT 5"
            ), {"u": uid, "sc": first_sc})).all()
            print(f"\n=== First 5 / Last 5 txns for {first_sc} ===")
            for d, nv, un, am in first_txn:
                print(f"  FIRST  {d}  nav={float(nv):>10.4f}  units={float(un):>10.4f}  amount={float(am):>10,.2f}")
            for d, nv, un, am in last_txn:
                print(f"  LAST   {d}  nav={float(nv):>10.4f}  units={float(un):>10.4f}  amount={float(am):>10,.2f}")
    await dispose_engine()


if __name__ == "__main__":
    asyncio.run(_run())
