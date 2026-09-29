"""The one place a CAS ingest asks for a net-worth rebuild.

Every import path — the CAMS/KFintech PDF upload, the MF Central QR import, any future
source — calls this, so the conditions cannot drift apart between them. The MFC router
on ``feat/mfc-cas-integration`` duplicated the router's inline kickoff, which is exactly
how two paths end up disagreeing about when a rebuild is due.

Two rules that are easy to get wrong:

**Enqueue whenever the ingest did not fail — not only when rows were inserted.** The old
condition was ``mf_transactions_inserted > 0``, which quietly skips the case that needs a
rebuild most: a re-upload that *corrects* or *narrows* the ledger inserts zero new rows
and still changes every number on the chart. "I re-uploaded and it still shows the old
figures" traces straight back to that check.

**Enqueue after the final commit.** The ingest commits four separate times, and the
snapshot is only promoted to ``active`` in the last one. A background task started before
that races the promotion and pins the *previous* statement — building the series the
upload was meant to replace.
"""

from __future__ import annotations

import logging
import uuid
from typing import Optional, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.portfolio.services.networth import job as job_store
from app.domains.portfolio.services.networth.builder import rebuild_user_networth

logger = logging.getLogger(__name__)


class _TaskQueue(Protocol):
    """Just the slice of ``fastapi.BackgroundTasks`` we need."""

    def add_task(self, func, /, *args, **kwargs) -> None: ...


async def schedule_rebuild_after_cas(
    db: AsyncSession,
    background: _TaskQueue,
    user_id: uuid.UUID,
    *,
    ingest_failed: bool,
    trigger: str = "cas_upload",
) -> Optional[uuid.UUID]:
    """Queue a full net-worth rebuild for a user whose statement just landed.

    Best-effort by design: a failed kickoff must never fail the upload the user just
    completed successfully. Returns the job id when one was queued or joined.

    When a build is already in flight it is *flagged to re-run* rather than joined —
    that build is reading the statement this upload just superseded, so letting it
    finish unchallenged would leave the user looking at data they already replaced.
    """
    if ingest_failed:
        return None

    try:
        job, created = await job_store.create_job(
            db, user_id, trigger=trigger, supersede=True
        )
    except Exception:  # noqa: BLE001 — never fail an upload on the follow-up work
        logger.exception(
            "could not queue net-worth rebuild after CAS ingest for user %s", user_id
        )
        return None

    if created:
        background.add_task(rebuild_user_networth, user_id, job.id, trigger=trigger)
    else:
        logger.info(
            "net-worth build already running for user %s; job %s will re-run",
            user_id,
            job.id,
        )
    return job.id
