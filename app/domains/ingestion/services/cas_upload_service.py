"""Lifecycle of a CAS snapshot: mint → activate → supersede.

This is the write side of the model described in ``app/core/cas_scope.py``. It is
the only module that changes ``cas_uploads.status``; everything else asks
:func:`~app.core.cas_scope.resolve_active_cas_upload_id` which snapshot is live.

The ordering rule that matters: ``activate`` demotes the previous ACTIVE row and
promotes the new one inside ONE transaction. The partial unique index
(``uq_cas_uploads_one_active``) makes any other ordering fail loudly rather than
leave a user with two live snapshots.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import Uuid, bindparam, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.ingestion.models.cas_upload import CasUpload, CasUploadStatus

logger = logging.getLogger(__name__)

# Every table stamped with ``cas_upload_id``, and how a row reaches its user.
# Used only by the adoption/backfill pass — normal writes are stamped by the
# ``before_flush`` hook in app/core/cas_scope.py.
_ADOPT_BY_USER_ID: tuple[str, ...] = (
    "mf_aa_imports",
    "mf_transactions",
    "mf_sip_mandates",
    "user_mf_latest_snapshot",
    "portfolio_allocation_snapshots",
    "funds",
    "user_investment_lists",
    "portfolio_networth_jobs",
    "user_portfolio_nav_history",
    "asset_allocation_runs",
    "practical_asset_allocation_runs",
    "rebalancing_runs",
    "additional_investment_runs",
    "cashflow_plan_runs",
    "chat_ai_module_runs",
)
# Portfolio children hang off the (single, unversioned) portfolio container.
_ADOPT_BY_PORTFOLIO: tuple[str, ...] = (
    "portfolio_holdings",
    "portfolio_allocations",
    "portfolio_history",
)

SCOPED_TABLES: tuple[str, ...] = _ADOPT_BY_USER_ID + _ADOPT_BY_PORTFOLIO


def sha256_of(file_bytes: bytes) -> str:
    return hashlib.sha256(file_bytes).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _uuid_params(**values: uuid.UUID):
    """Bind UUIDs through a typed parameter so raw ``text()`` works on any dialect.

    asyncpg refuses a plain string for a uuid column and the sqlite driver refuses
    a UUID object for anything — an explicit ``Uuid`` type keeps one statement
    correct on both.
    """
    return [
        bindparam(name, value=value, type_=Uuid(as_uuid=True))
        for name, value in values.items()
    ]


async def get_active(db: AsyncSession, user_id: uuid.UUID) -> Optional[CasUpload]:
    """The user's live snapshot row, or None if they have never uploaded a CAS."""
    return (
        (
            await db.execute(
                select(CasUpload).where(
                    CasUpload.user_id == user_id,
                    CasUpload.status == CasUploadStatus.ACTIVE.value,
                )
            )
        )
        .scalars()
        .first()
    )


async def find_identical_active(
    db: AsyncSession, user_id: uuid.UUID, content_sha256: str
) -> Optional[CasUpload]:
    """The active snapshot when it came from this exact file.

    Re-uploading the byte-identical PDF is common (the user is not sure it
    worked). Reprocessing it would burn a paid casparser call and produce a
    snapshot indistinguishable from the live one, so the ingest short-circuits.
    """
    active = await get_active(db, user_id)
    if active is not None and active.content_sha256 == content_sha256:
        return active
    return None


async def mint(
    db: AsyncSession,
    user_id: uuid.UUID,
    *,
    content_sha256: Optional[str] = None,
    source_filename: Optional[str] = None,
    file_size_bytes: Optional[int] = None,
) -> CasUpload:
    """Create a PARSING snapshot. Does not commit and does not touch the live one."""
    next_seq = int(
        (
            await db.execute(
                select(func.coalesce(func.max(CasUpload.seq), 0) + 1).where(
                    CasUpload.user_id == user_id
                )
            )
        ).scalar_one()
    )
    snapshot = CasUpload(
        user_id=user_id,
        seq=next_seq,
        status=CasUploadStatus.PARSING.value,
        content_sha256=content_sha256,
        source_filename=(source_filename or None),
        file_size_bytes=file_size_bytes,
    )
    db.add(snapshot)
    await db.flush()
    logger.info(
        "cas_upload minted id=%s seq=%d user=%s", snapshot.id, next_seq, user_id
    )
    return snapshot


async def activate(
    db: AsyncSession,
    snapshot: CasUpload,
    *,
    cas_type: Optional[str] = None,
    file_type: Optional[str] = None,
    statement_from: Optional[str] = None,
    statement_to: Optional[str] = None,
    folios: Optional[int] = None,
    schemes: Optional[int] = None,
    transactions: Optional[int] = None,
    total_value_inr: Optional[float] = None,
    total_invested_inr: Optional[float] = None,
    mf_aa_import_id: Optional[uuid.UUID] = None,
    cas_document_id: Optional[uuid.UUID] = None,
) -> CasUpload:
    """Make ``snapshot`` the live one and supersede whatever held that slot.

    Demote first, promote second: the partial unique index rejects the reverse
    order, which is the point — a bug here fails the upload instead of silently
    leaving two active snapshots and an app that shows a mixture of both.
    """
    now = _now()
    await db.execute(
        update(CasUpload)
        .where(
            CasUpload.user_id == snapshot.user_id,
            CasUpload.status == CasUploadStatus.ACTIVE.value,
            CasUpload.id != snapshot.id,
        )
        .values(
            status=CasUploadStatus.SUPERSEDED.value,
            superseded_at=now,
            superseded_by_id=snapshot.id,
        )
    )
    snapshot.status = CasUploadStatus.ACTIVE.value
    snapshot.activated_at = now
    snapshot.cas_type = cas_type or snapshot.cas_type
    snapshot.file_type = file_type or snapshot.file_type
    snapshot.statement_from = statement_from or snapshot.statement_from
    snapshot.statement_to = statement_to or snapshot.statement_to
    snapshot.folios = folios if folios is not None else snapshot.folios
    snapshot.schemes = schemes if schemes is not None else snapshot.schemes
    snapshot.transactions = (
        transactions if transactions is not None else snapshot.transactions
    )
    snapshot.total_value_inr = (
        total_value_inr if total_value_inr is not None else snapshot.total_value_inr
    )
    snapshot.total_invested_inr = (
        total_invested_inr
        if total_invested_inr is not None
        else snapshot.total_invested_inr
    )
    snapshot.mf_aa_import_id = mf_aa_import_id or snapshot.mf_aa_import_id
    snapshot.cas_document_id = cas_document_id or snapshot.cas_document_id
    await db.flush()
    logger.info(
        "cas_upload activated id=%s seq=%d user=%s value=%s",
        snapshot.id,
        snapshot.seq,
        snapshot.user_id,
        snapshot.total_value_inr,
    )
    return snapshot


async def mark_failed(
    db: AsyncSession, snapshot_id: uuid.UUID, reason: str, *, commit: bool = True
) -> None:
    """Record a failed ingest without disturbing the live snapshot.

    Best-effort by design: this runs in the failure path, and an error here must
    not replace the real exception the caller is about to raise.
    """
    try:
        await db.rollback()
        await db.execute(
            update(CasUpload)
            .where(
                CasUpload.id == snapshot_id,
                CasUpload.status == CasUploadStatus.PARSING.value,
            )
            .values(
                status=CasUploadStatus.FAILED.value,
                failure_reason=(reason or "")[:2000] or None,
            )
        )
        if commit:
            await db.commit()
    except Exception:  # noqa: BLE001
        logger.exception("could not mark cas_upload %s failed", snapshot_id)


async def adopt_unscoped_rows(
    db: AsyncSession, user_id: uuid.UUID, cas_upload_id: uuid.UUID
) -> dict[str, int]:
    """Claim every unstamped row of a user's derived data for ``cas_upload_id``.

    Two callers, one purpose — make sure a user's pre-existing data belongs to a
    snapshot BEFORE a second one exists, so the two are never summed together:

      * the backfill script, run once at rollout;
      * the ingest itself, when a user with data has no active snapshot yet
        (which makes the feature self-healing if the script was never run).

    Rows already carrying an id are never re-stamped: they belong to the
    snapshot that produced them, superseded or not.
    """
    counts: dict[str, int] = {}
    for table in _ADOPT_BY_USER_ID:
        result = await db.execute(
            text(
                f"UPDATE {table} SET cas_upload_id = :cid "
                f"WHERE user_id = :uid AND cas_upload_id IS NULL"
            ).bindparams(*_uuid_params(cid=cas_upload_id, uid=user_id))
        )
        if result.rowcount:
            counts[table] = int(result.rowcount)
    for table in _ADOPT_BY_PORTFOLIO:
        result = await db.execute(
            text(
                f"UPDATE {table} SET cas_upload_id = :cid "
                f"WHERE cas_upload_id IS NULL AND portfolio_id IN "
                f"(SELECT id FROM portfolios WHERE user_id = :uid)"
            ).bindparams(*_uuid_params(cid=cas_upload_id, uid=user_id))
        )
        if result.rowcount:
            counts[table] = int(result.rowcount)
    await db.flush()
    if counts:
        logger.info(
            "adopted %d unscoped rows across %d tables into cas_upload %s (user %s)",
            sum(counts.values()),
            len(counts),
            cas_upload_id,
            user_id,
        )
    return counts


async def ensure_legacy_snapshot(
    db: AsyncSession, user_id: uuid.UUID
) -> Optional[CasUpload]:
    """Give a user's pre-feature data a home before a new statement arrives.

    Returns the snapshot that now owns it, or None when the user has nothing to
    adopt. Called by the ingest when there is no active snapshot: without it, the
    old rows would stay NULL — permanently visible under THE NULL RULE — and be
    counted alongside the incoming statement.
    """
    has_data = (
        await db.execute(
            text(
                "SELECT 1 FROM mf_transactions WHERE user_id = :uid "
                "AND cas_upload_id IS NULL LIMIT 1"
            ).bindparams(*_uuid_params(uid=user_id))
        )
    ).first()
    if has_data is None:
        # Nothing from a previous life; check the portfolio side too, since a
        # user can hold allocations without a normalized ledger.
        has_data = (
            await db.execute(
                text(
                    "SELECT 1 FROM portfolio_holdings h "
                    "JOIN portfolios p ON h.portfolio_id = p.id "
                    "WHERE p.user_id = :uid AND h.cas_upload_id IS NULL LIMIT 1"
                ).bindparams(*_uuid_params(uid=user_id))
            )
        ).first()
    if has_data is None:
        return None

    legacy = await mint(db, user_id, source_filename="(pre-versioning data)")
    await adopt_unscoped_rows(db, user_id, legacy.id)
    legacy.status = CasUploadStatus.ACTIVE.value
    legacy.activated_at = _now()
    legacy.failure_reason = None
    await db.flush()
    logger.info("legacy snapshot %s created for user %s", legacy.id, user_id)
    return legacy
