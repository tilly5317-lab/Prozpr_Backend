#!/usr/bin/env python3
"""Re-mark every user's primary portfolio to their ACTIVE CAS snapshot and clear
computed plans, via the same service the CAMS ingest runs after activation.

Why a script at all: ``portfolios.total_value`` is not snapshot-scoped and is
what the allocation engine reads as the corpus. Rows written before the
post-ingest invalidation existed (or by a process without the scope listeners)
sum EVERY snapshot the user ever uploaded.

Scripts run outside a request, so the CAS-scope SELECT filter is NOT active
until ``install_cas_scope_listeners()`` is called — without it this script would
write the all-snapshots sum back again.
"""

import asyncio
import logging

from sqlalchemy import text

import app.all_models  # noqa: F401 — register every ORM model before any query
from app.core.cas_scope import install_cas_scope_listeners
from app.core.database import _get_session_factory
from app.domains.ingestion.services.cache_invalidation_service import (
    invalidate_user_caches,
)

logging.basicConfig(level=logging.INFO)
logging.getLogger("sqlalchemy").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


async def main() -> None:
    install_cas_scope_listeners()
    factory = _get_session_factory()

    async with factory() as db:
        user_ids = (
            (
                await db.execute(
                    text(
                        "SELECT DISTINCT user_id FROM portfolios "
                        "WHERE is_primary = TRUE ORDER BY user_id"
                    )
                )
            )
            .scalars()
            .all()
        )

    logger.info("re-marking %d portfolios", len(user_ids))
    failed = 0
    for user_id in user_ids:
        async with factory() as db:
            summary = await invalidate_user_caches(db, user_id)
            await db.commit()
        if summary["error"]:
            failed += 1
            logger.warning("FAILED %s: %s", user_id, summary["error"])
        else:
            logger.info(
                "%s value=%s cleared alloc=%d rebal=%d",
                user_id,
                summary["portfolio_total_value"],
                summary["allocations_cleared"],
                summary["rebalancing_runs_cleared"],
            )
    logger.info("done: %d ok, %d failed", len(user_ids) - failed, failed)


if __name__ == "__main__":
    asyncio.run(main())
