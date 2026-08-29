"""Erasure: soft-delete now, hard purge after the grace window.

Two stages on purpose.

*Immediately*, the account is tombstoned in place — identity columns are
overwritten, the login credential is destroyed and the row stops authenticating.
From the user's side the account is gone the moment they ask, which is what the
right actually promises.

*After the grace window*, a purge job removes the rows themselves along with the
files that no foreign key can reach. The delay is not reluctance: erasure is
irreversible, "delete my account" is a common misclick, and a purge that runs
inside the request would have to hold a transaction across S3 calls.

Nothing here is a legal retention hold. Prozpr is not SEBI-registered, so no
statutory floor forces us to keep KYC or transaction records; if that changes,
the carve-out belongs in ``user_graph`` as a skip list, not as a longer window.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.identity.models.user import User
from app.domains.privacy.models.consent import DeletedUserTombstone
from app.domains.privacy.services.user_graph import delete_user_rows

logger = logging.getLogger(__name__)

#: How long a soft-deleted account stays recoverable.
GRACE_DAYS = 30


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def request_erasure(db: AsyncSession, user: User) -> datetime:
    """Stage one. Returns when the hard purge becomes due.

    The identity columns are overwritten here rather than at purge time so that
    the window is not a period in which a "deleted" account still holds a
    readable name, PAN and phone number.
    """
    if user.deleted_at is not None and user.deletion_scheduled_for is not None:
        return user.deletion_scheduled_for  # idempotent: already requested

    now = _now()
    due = now + timedelta(days=GRACE_DAYS)

    user.deleted_at = now
    user.deletion_scheduled_for = due

    # Unique columns get a per-user placeholder rather than NULL: `phone` is NOT
    # NULL and unique, so two tombstoned accounts would collide on an empty
    # string and abort the second deletion.
    stub = uuid.uuid4().hex[:12]
    user.pan = None
    user.first_name = None
    user.middle_name = None
    user.last_name = None
    user.date_of_birth = None
    user.address = None
    user.occupation = None
    user.family_status = None
    user.email = None
    user.mobile = "deleted"
    user.phone = f"deleted-{stub}"
    # Destroy the credential outright — a soft-deleted account must not be able
    # to sign back in and quietly cancel its own erasure.
    user.password_hash = None
    user.pin_reset_code_hash = None
    user.pin_reset_expires_at = None
    user.is_active = False

    await db.flush()
    logger.info("Erasure requested; purge due %s", due.isoformat())
    return due


async def due_for_purge(db: AsyncSession, *, now: datetime | None = None) -> list[uuid.UUID]:
    """Accounts whose grace window has expired."""
    cutoff = now or _now()
    rows = (
        await db.execute(
            select(User.id).where(
                User.deleted_at.isnot(None),
                User.deletion_scheduled_for.isnot(None),
                User.deletion_scheduled_for <= cutoff,
            )
        )
    ).scalars()
    return list(rows)


async def purge_user(
    db: AsyncSession, user_id: uuid.UUID, *, dry_run: bool = False
) -> dict[str, int]:
    """Stage two. Deletes the rows, the S3 statement archive and the tombstone note.

    Does NOT commit — the caller owns the transaction so the purge and its
    tombstone land atomically.
    """
    requested_at = (
        await db.execute(select(User.deleted_at).where(User.id == user_id))
    ).scalar_one_or_none() or _now()

    # Files first: they are outside the database, so a failure here must not be
    # hidden behind a transaction that later rolls back. Best-effort — an S3
    # outage cannot be allowed to block the erasure indefinitely.
    archived = await _delete_archived_statements(db, user_id, dry_run=dry_run)

    counts = await delete_user_rows(db, user_id, dry_run=dry_run)

    if not dry_run:
        detail = json.dumps({**counts, "_s3_statements": archived}, sort_keys=True)
        # `user_id` is the tombstone's primary key, and this can legitimately run
        # twice for one account: once at the scheduled purge, and again via
        # `reapply_tombstones` after a database restore resurrects the rows. The
        # second run must update the existing note, not collide with it.
        existing = await db.get(DeletedUserTombstone, user_id)
        if existing is None:
            db.add(
                DeletedUserTombstone(
                    user_id=user_id,
                    requested_at=requested_at,
                    purged_at=_now(),
                    rows_deleted=detail,
                )
            )
        else:
            existing.purged_at = _now()
            existing.rows_deleted = detail
            existing.reason = "restore_reapply"
        await db.flush()
    return counts


async def _delete_archived_statements(
    db: AsyncSession, user_id: uuid.UUID, *, dry_run: bool
) -> int:
    """Remove the user's CAS PDFs from S3.

    These are the one store no cascade reaches: the objects live under
    ``user-cas/{user_id}/`` and only ``user_cas_documents.s3_key`` points at
    them, so deleting the row without this step orphans a PDF containing the
    person's name, PAN, address and full transaction ledger — forever, since
    that prefix has no lifecycle rule.
    """
    keys = [
        k
        for k in (
            await db.execute(
                text("SELECT s3_key FROM user_cas_documents WHERE user_id = :uid"),
                {"uid": user_id},
            )
        ).scalars()
        if k
    ]
    if not keys or dry_run:
        return len(keys)

    deleted = 0
    try:
        from app.domains.ingestion.services.cams_pdf_stage import delete_archived_cas

        for key in keys:
            try:
                delete_archived_cas(key)
                deleted += 1
            except Exception:
                logger.warning("Could not delete archived statement during erasure.")
    except Exception:
        logger.exception("Statement archive unavailable during erasure.")
    return deleted


async def reapply_tombstones(db: AsyncSession, *, dry_run: bool = False) -> list[uuid.UUID]:
    """Re-purge anyone a database restore brought back.

    An RDS snapshot is a point-in-time copy, so restoring one from before an
    erasure resurrects the account. ``deleted_user_tombstones`` is excluded from
    the purge precisely so it can outlive the rows and drive this. Run it after
    every restore, before reopening the app.
    """
    tombstoned = (await db.execute(select(DeletedUserTombstone.user_id))).scalars().all()
    if not tombstoned:
        return []
    alive = (
        (await db.execute(select(User.id).where(User.id.in_(tombstoned)))).scalars().all()
    )
    for user_id in alive:
        await purge_user(db, user_id, dry_run=dry_run)
    if alive:
        logger.warning(
            "Re-applied erasure to %d account(s) resurrected by a restore.", len(alive)
        )
    return list(alive)
