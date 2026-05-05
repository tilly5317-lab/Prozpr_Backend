#!/usr/bin/env python3
"""Delete schemes with no NAV in the last N days and delete their NAV history.

Run from ``Prozpr_Backend/``:

    python scripts/delete_stale_schemes.py
    python scripts/delete_stale_schemes.py --window-days 30 --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import date, timedelta
from pathlib import Path

from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import _get_session_factory


async def _run(window_days: int, dry_run: bool) -> int:
    cutoff = date.today() - timedelta(days=window_days)
    factory = _get_session_factory()
    async with factory() as db:
        stale_codes_stmt = text(
            "SELECT m.scheme_code "
            "FROM mf_fund_metadata m "
            "LEFT JOIN ("
            "  SELECT scheme_code, MAX(nav_date) AS max_nav_date "
            "  FROM mf_nav_history "
            "  GROUP BY scheme_code"
            ") h ON h.scheme_code = m.scheme_code "
            "WHERE h.max_nav_date IS NULL OR h.max_nav_date < :cutoff"
        )
        stale_rows = await db.execute(stale_codes_stmt, {"cutoff": cutoff})
        stale_codes = [str(r[0]) for r in stale_rows.all() if str(r[0]).strip()]

        if not stale_codes:
            print(f"No stale schemes found for cutoff {cutoff}.")
            return 0

        nav_count_stmt = text("SELECT COUNT(*) FROM mf_nav_history WHERE scheme_code = ANY(:codes)")
        txn_count_stmt = text("SELECT COUNT(*) FROM mf_transactions WHERE scheme_code = ANY(:codes)")
        sip_count_stmt = text("SELECT COUNT(*) FROM mf_sip_mandates WHERE scheme_code = ANY(:codes)")
        nav_count_row = await db.execute(nav_count_stmt, {"codes": stale_codes})
        txn_count_row = await db.execute(txn_count_stmt, {"codes": stale_codes})
        sip_count_row = await db.execute(sip_count_stmt, {"codes": stale_codes})
        nav_rows_to_delete = int(nav_count_row.scalar_one() or 0)
        txn_rows_to_delete = int(txn_count_row.scalar_one() or 0)
        sip_rows_to_delete = int(sip_count_row.scalar_one() or 0)

        print(
            "Stale schemes: "
            f"{len(stale_codes)} | NAV rows: {nav_rows_to_delete} | "
            f"MF transactions: {txn_rows_to_delete} | SIP mandates: {sip_rows_to_delete} | "
            f"cutoff: {cutoff}"
        )

        if dry_run:
            await db.rollback()
            print("Dry run only. No rows deleted.")
            return 0

        delete_nav_stmt = text("DELETE FROM mf_nav_history WHERE scheme_code = ANY(:codes)")
        delete_txn_stmt = text("DELETE FROM mf_transactions WHERE scheme_code = ANY(:codes)")
        delete_sip_stmt = text("DELETE FROM mf_sip_mandates WHERE scheme_code = ANY(:codes)")
        delete_meta_stmt = text("DELETE FROM mf_fund_metadata WHERE scheme_code = ANY(:codes)")

        nav_res = await db.execute(delete_nav_stmt, {"codes": stale_codes})
        txn_res = await db.execute(delete_txn_stmt, {"codes": stale_codes})
        sip_res = await db.execute(delete_sip_stmt, {"codes": stale_codes})
        meta_res = await db.execute(delete_meta_stmt, {"codes": stale_codes})
        await db.commit()

        print(
            "Deleted "
            f"{int(meta_res.rowcount or 0)} schemes, "
            f"{int(nav_res.rowcount or 0)} NAV rows, "
            f"{int(txn_res.rowcount or 0)} MF transactions, and "
            f"{int(sip_res.rowcount or 0)} SIP mandates."
        )
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--window-days",
        type=int,
        default=30,
        help="Schemes with latest NAV older than this many days are deleted.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show counts only; do not delete anything.",
    )
    args = parser.parse_args()
    raise SystemExit(asyncio.run(_run(window_days=args.window_days, dry_run=args.dry_run)))


if __name__ == "__main__":
    main()
