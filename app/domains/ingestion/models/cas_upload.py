"""SQLAlchemy ORM model — `cas_upload.py`.

One row per CAMS/KFintech CAS upload. Its ``id`` is the *snapshot key* that every
row derived from that statement carries (``cas_upload_id``), which is what lets a
re-upload replace what the app shows without deleting what came before.

Before this table existed, ``ingest_cams_pdf`` called
:func:`~app.domains.ingestion.services.user_data_reset.reset_user_financial_data`
on every upload — 46 DELETEs across 44 tables — so the statement archive survived
but every figure ever computed from it did not. Nothing could be compared across
statements: not allocation drift, not net-worth growth, and not whether the user
ever executed the rebalancing plan we gave them.

THE INVARIANT: exactly one ``active`` upload per user, enforced by Postgres, not
by application code::

    CREATE UNIQUE INDEX uq_cas_uploads_one_active
      ON cas_uploads (user_id) WHERE status = 'active';

Everything the app reads is scoped to that active row (see ``app/core/cas_scope.py``).
Superseded rows and their data stay in place, queryable, forever.

Deliberately SEPARATE from ``user_cas_documents``: that table is the archived PDF
(one row per file kept in S3). This one is the *ingest* — the parsed result, its
headline figures, and the version key. A snapshot points at its document when the
best-effort archive succeeded, and stands on its own when it didn't.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, declared_attr, mapped_column

from app.core.database import Base


class CasUploadStatus(str, enum.Enum):
    """Lifecycle of one upload.

    ``PARSING`` → ``ACTIVE`` on a clean ingest (demoting the previous ACTIVE to
    ``SUPERSEDED`` in the same transaction), or → ``FAILED`` if anything between
    the mint and the activation raises. A statement that never reaches ACTIVE
    leaves the user's live snapshot exactly as it was.
    """

    PARSING = "parsing"
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    FAILED = "failed"


CAS_UPLOAD_STATUSES: frozenset[str] = frozenset(s.value for s in CasUploadStatus)


class CasScoped:
    """Mixin: rows of this table belong to one CAS snapshot.

    Defines ``cas_upload_id`` once for all 18 stamped tables, and — more
    importantly — gives ``app/core/cas_scope.py`` a single class to hand
    ``with_loader_criteria``, so the read scope is applied to every mapped
    subclass by one rule instead of 56 hand-edited query sites.

    The column is nullable on purpose, and the read scope treats NULL as
    "always visible". NULL means "not owned by any statement", which covers
    three real cases that must never disappear from the app:

      * rows created before this feature shipped and not yet backfilled;
      * manually entered transactions and holdings (``source_system`` != AA);
      * anything synced from SimBanks/Finvu rather than a CAS.

    ``ON DELETE SET NULL`` rather than CASCADE: pruning a snapshot header must
    never be able to take a user's ledger with it. Per-user erasure deletes by
    ``user_id`` through the FK-graph walk in ``privacy/services/user_graph.py``,
    which reaches these rows independently of this column.
    """

    @declared_attr
    def cas_upload_id(cls) -> Mapped[Optional[uuid.UUID]]:  # noqa: N805
        return mapped_column(
            UUID(as_uuid=True),
            ForeignKey("cas_uploads.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        )


def scoped_table_names() -> tuple[str, ...]:
    """Every table stamped with ``cas_upload_id``, read off the mapper registry.

    The single source of truth for "which tables belong to a statement". The
    startup DDL derives its ALTER/CREATE INDEX loop from this, so adding the
    mixin to a new model is enough — there is no second list to forget, which is
    the drift that would leave a table stamped in Python and unstamped in
    Postgres. The adoption lists in ``cas_upload_service`` (which additionally
    need to know how each table reaches its user) are checked against this by
    ``test_cas_scope.py``.

    Requires the models to be imported — ``app.all_models`` guarantees that at
    boot, and callers here run well after it.
    """
    from app.core.database import Base

    return tuple(
        sorted(
            mapper.class_.__tablename__
            for mapper in Base.registry.mappers
            if issubclass(mapper.class_, CasScoped)
        )
    )


class CasUpload(Base):
    __tablename__ = "cas_uploads"
    __table_args__ = (
        Index("ix_cas_uploads_user_created", "user_id", "created_at"),
        # The one-active-per-user invariant is a PARTIAL unique index, which
        # SQLAlchemy cannot express portably here; it is created in
        # ``apply_postgres_schema_patches`` (app/core/database.py).
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Per-user 1, 2, 3 … so the app and support can say "upload #3" without
    # exposing a UUID. Assigned at mint time under the user's row lock.
    seq: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # One of CasUploadStatus. Stored as text (not a Postgres ENUM) so adding a
    # state later is an application change, not a type migration.
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=CasUploadStatus.PARSING.value, index=True
    )
    # sha256 of the uploaded PDF bytes. Re-uploading the byte-identical file the
    # user already has active short-circuits the whole ingest — no second
    # casparser call (they are billed), no duplicate snapshot.
    content_sha256: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    source_filename: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    file_size_bytes: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    cas_type: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    file_type: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    statement_from: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    statement_to: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)

    folios: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    schemes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    transactions: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # The snapshot's headline, denormalised here so the history chart never has
    # to touch — or keep — the ledger behind it.
    total_value_inr: Mapped[Optional[float]] = mapped_column(
        Numeric(18, 2), nullable=True
    )
    total_invested_inr: Mapped[Optional[float]] = mapped_column(
        Numeric(18, 2), nullable=True
    )

    # The archived PDF, when S3 archiving succeeded (it is best-effort).
    cas_document_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("user_cas_documents.id", ondelete="SET NULL"),
        nullable=True,
    )
    # The raw parsed import this snapshot produced.
    mf_aa_import_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    # Chains the history newest-last; NULL on the active row.
    superseded_by_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cas_uploads.id", ondelete="SET NULL"),
        nullable=True,
    )
    failure_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    activated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    superseded_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
