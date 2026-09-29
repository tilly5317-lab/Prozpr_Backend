#!/usr/bin/env python3
"""Replace TODAY's net-worth point for every user with the active-statement value.

    python scripts/reprice_networth_today.py                     # dry run: report only
    python scripts/reprice_networth_today.py --apply             # delete + re-insert today
    python scripts/reprice_networth_today.py --apply --trailing-days 7
                                                                 # also re-price the trailing week

What it repairs: today's row in ``user_portfolio_nav_history`` written by something
other than the net-worth package. On 2026-09-13 a process still running the pre-package
daily job (deleted in PR #87) rewrote today's point from the UNSCOPED holdings roll-up —
every statement the user ever uploaded, summed — minutes after the real daily pass had
priced it correctly, and without touching ``updated_at``. A user holding Rs 60,792
charted Rs 15.1 crore.

The correct value is what the daily job itself computes: the user's materialised
positions (written by the last rebuild, inside the active CAS scope) priced at the
latest NAV, plus the series anchor. This script runs exactly that statement, so the
number it writes is the number the next scheduled pass would write.

Users the pass cannot price — no positions or no series state, i.e. never rebuilt
under the package — lose their wrong row instead of keeping it; the daily job's
``_queue_rebuilds`` adopts them on its next run. They are listed at the end.

Scripts run outside a request: ``install_cas_scope_listeners()`` is called so any ORM
read below sees the active statement, and ``app.all_models`` is imported so mapper
configuration does not fail on the first relationship.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text  # noqa: E402

import app.all_models  # noqa: E402,F401
from app.core.cas_scope import install_cas_scope_listeners  # noqa: E402
from app.core.database import _get_session_factory, dispose_engine  # noqa: E402
from app.domains.portfolio.services.networth.clock import ist_today  # noqa: E402
from app.domains.portfolio.services.networth.daily import (  # noqa: E402
    refresh_day,
    rewrite_day,
)

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s"
)
logging.getLogger("sqlalchemy").setLevel(logging.WARNING)
logger = logging.getLogger("reprice_networth_today")

# Stored vs. what the daily statement prices: positions x latest NAV + anchor.
_AUDIT = text(
    """
    WITH priced AS (
        SELECT p.user_id, SUM(p.units * n.nav) AS v
        FROM   user_scheme_position p
        JOIN   LATERAL (
                   SELECT m.nav FROM mf_nav_history m
                   WHERE  m.scheme_code = p.nav_key
                     AND  m.nav_date <= :as_of AND m.nav > 0
                   ORDER  BY m.nav_date DESC LIMIT 1
               ) n ON TRUE
        WHERE  p.units > 0.000001 AND p.nav_key IS NOT NULL
        GROUP  BY p.user_id
    )
    SELECT h.user_id,
           h.total_value                       AS stored,
           h.total_invested                    AS stored_invested,
           round(pr.v + s.anchor_value, 2)     AS expected,
           s.last_invested                     AS expected_invested,
           (SELECT count(*) FROM cas_uploads c WHERE c.user_id = h.user_id) AS uploads,
           (SELECT count(*) FROM portfolio_networth_jobs j
             WHERE j.user_id = h.user_id AND j.status IN ('pending', 'running')) AS live_jobs
    FROM   user_portfolio_nav_history h
    LEFT   JOIN user_networth_series_state s ON s.user_id = h.user_id
    LEFT   JOIN priced pr ON pr.user_id = h.user_id
    WHERE  h.recorded_date = :as_of
    ORDER  BY uploads DESC, h.user_id
    """
)


def _off(stored, expected) -> bool:
    if expected is None:
        return True
    return abs(float(stored) - float(expected)) > 0.01


async def audit(db, as_of: date) -> tuple[list, list]:
    rows = (await db.execute(_AUDIT, {"as_of": as_of})).mappings().all()
    wrong = [
        r
        for r in rows
        if _off(r["stored"], r["expected"])
        or _off(r["stored_invested"], r["expected_invested"])
    ]
    return list(rows), wrong


def _print_audit(label: str, rows: list, wrong: list) -> None:
    logger.info(
        "%s: %d users have a row for today, %d are wrong", label, len(rows), len(wrong)
    )
    for r in wrong:
        logger.info(
            "  %s uploads=%s live_jobs=%s stored=%s (invested %s) expected=%s (invested %s)",
            str(r["user_id"])[:8],
            r["uploads"],
            r["live_jobs"],
            r["stored"],
            r["stored_invested"],
            r["expected"] if r["expected"] is not None else "UNPRICEABLE",
            r["expected_invested"],
        )


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--apply", action="store_true", help="write; default is a dry run"
    )
    parser.add_argument(
        "--trailing-days",
        type=int,
        default=0,
        help="also re-price this many days before today (upsert, no delete)",
    )
    args = parser.parse_args()

    install_cas_scope_listeners()
    today = ist_today()
    factory = _get_session_factory()
    try:
        async with factory() as db:
            rows_before, wrong_before = await audit(db, today)
            _print_audit(f"before ({today})", rows_before, wrong_before)
            if not args.apply:
                logger.info("dry run — re-run with --apply to rewrite today's rows")
                return 0

            for offset in range(args.trailing_days, 0, -1):
                day = today - timedelta(days=offset)
                written = await refresh_day(db, day)
                logger.info("re-priced %s for %d users", day, written)

            deleted, written = await rewrite_day(db, today)
            logger.info(
                "today %s: deleted %d rows, wrote %d rows", today, deleted, written
            )

            rows_after, wrong_after = await audit(db, today)
            _print_audit(f"after ({today})", rows_after, wrong_after)

            had = {r["user_id"] for r in rows_before}
            have = {r["user_id"] for r in rows_after}
            for uid in sorted(had - have, key=str):
                logger.warning(
                    "  %s had a row for today and now has none: no priced positions "
                    "or no series state — the daily job's rebuild queue adopts them",
                    str(uid)[:8],
                )
            return 1 if wrong_after else 0
    finally:
        await dispose_engine()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
