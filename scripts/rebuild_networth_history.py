"""Rebuild net-worth history from the transaction ledger, for one user or the fleet.

There was no repair tool for this before: the only way to recompute a user's series was
to be that user and press a button. That is a bad place to be when a bug ships, when a
NAV backfill lands, or when a data fix needs verifying on a real account before it goes
anywhere near production.

    # See what would happen, for everyone
    python scripts/rebuild_networth_history.py --all --dry-run

    # Rebuild one account and print the resulting series bounds
    python scripts/rebuild_networth_history.py --user 0e3f...  --verbose

    # Rebuild everyone whose series is stale or missing, oldest first, 3 at a time
    python scripts/rebuild_networth_history.py --all --stale-only --concurrency 3

Safe to run against production traffic: every rebuild takes the same per-user advisory
transaction lock the live path does, so a script run and a user's own upload serialise
instead of corrupting each other. Failures are per-user — one bad account never stops
the sweep.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text  # noqa: E402

# Every ORM class, not only the ones this script names. SQLAlchemy configures mappers
# as a set, so the first relationship pointing at a model nothing imported raises
# "failed to locate a name ('User')" on the first query — which made this tool fail on
# every account it was pointed at.
import app.all_models  # noqa: E402,F401
from app.core.cas_scope import (  # noqa: E402
    effective_scope,
    install_cas_scope_listeners,
    scoped_to,
)
from app.core.database import _get_session_factory, dispose_engine  # noqa: E402
from app.domains.portfolio.services.networth.builder import build_series  # noqa: E402
from app.domains.portfolio.services.networth.clock import ist_today  # noqa: E402

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s"
)
logger = logging.getLogger("rebuild_networth")

# Users with a transaction ledger, and how their series currently stands.
_CANDIDATES = text(
    """
    SELECT t.user_id,
           s.first_date, s.last_date, s.row_count
    FROM   (SELECT DISTINCT user_id FROM mf_transactions) t
    LEFT   JOIN user_networth_series_state s ON s.user_id = t.user_id
    ORDER  BY s.last_date NULLS FIRST
    """
)


async def _candidates(stale_only: bool, limit: int | None) -> list[uuid.UUID]:
    factory = _get_session_factory()
    async with factory() as db:
        rows = (await db.execute(_CANDIDATES)).all()
    today = ist_today()
    out: list[uuid.UUID] = []
    for row in rows:
        if stale_only and row.last_date is not None and row.last_date >= today:
            continue
        out.append(row.user_id)
        if limit is not None and len(out) >= limit:
            break
    return out


async def _rebuild_one(user_id: uuid.UUID, verbose: bool) -> tuple[uuid.UUID, str]:
    """Rebuild one user. Never raises — the sweep must survive a bad account."""
    factory = _get_session_factory()
    try:
        async with factory() as scope_db:
            snapshot_id = await effective_scope(scope_db, user_id)
        # Without pinning the snapshot, reads span every statement the user has ever
        # uploaded and the series is built from double-counted units. ``scoped_to``
        # only sets the contextvar — the ORM hook that READS it is installed by the
        # app's lifespan, which never runs here, so this script has to install it
        # itself. Skipping that is silent: the rebuild succeeds and writes a series
        # summed over every statement the user ever uploaded (measured on a real
        # account: 6 schemes and Rs 61k became 67 schemes and Rs 8.9 crore).
        install_cas_scope_listeners()
        with scoped_to(snapshot_id):
            async with factory() as db:
                result = await build_series(db, user_id, snapshot_id)
        detail = (
            f"{result.days_written} days, {result.schemes} schemes, "
            f"{result.degraded_schemes} degraded, "
            f"complete={result.ledger_complete}"
        )
        if verbose and result.warnings:
            detail += f", warnings={result.warnings}"
        return user_id, detail
    except Exception as exc:  # noqa: BLE001 — one bad account must not stop the sweep
        logger.exception("rebuild failed for user %s", user_id)
        return user_id, f"FAILED: {exc}"


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--user", help="rebuild a single user id")
    target.add_argument(
        "--all", action="store_true", help="rebuild every user with transactions"
    )
    parser.add_argument(
        "--stale-only",
        action="store_true",
        help="with --all, skip users whose series already reaches today",
    )
    parser.add_argument("--limit", type=int, default=None, help="cap users processed")
    parser.add_argument(
        "--concurrency",
        type=int,
        default=1,
        help="users rebuilt in parallel (each holds a DB connection; keep it small)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="list who would be rebuilt, and change nothing",
    )
    parser.add_argument("--verbose", action="store_true", help="include warning counts")
    args = parser.parse_args()

    try:
        if args.user:
            targets = [uuid.UUID(args.user)]
        else:
            targets = await _candidates(args.stale_only, args.limit)

        logger.info("%d user(s) selected", len(targets))
        if args.dry_run:
            for user_id in targets:
                logger.info("would rebuild %s", user_id)
            return 0

        sem = asyncio.Semaphore(max(1, args.concurrency))
        done = 0
        failed = 0

        async def _one(user_id: uuid.UUID) -> None:
            nonlocal done, failed
            async with sem:
                _, detail = await _rebuild_one(user_id, args.verbose)
            done += 1
            if detail.startswith("FAILED"):
                failed += 1
            logger.info("[%d/%d] %s -> %s", done, len(targets), user_id, detail)

        await asyncio.gather(*(_one(user_id) for user_id in targets))
        logger.info("finished: %d rebuilt, %d failed", done - failed, failed)
        return 1 if failed else 0
    finally:
        await dispose_engine()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
