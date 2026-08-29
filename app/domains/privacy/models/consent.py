"""ORM models — DPDP consent, notice versions, grievances, erasure tombstones.

Four tables, one job each:

* ``consent_records`` — an APPEND-ONLY ledger. Withdrawal is a new row, never an
  update, because the obligation is to show what a person agreed to *and when*,
  including things they have since revoked. An UPDATE would destroy exactly the
  evidence the ledger exists to hold.
* ``privacy_policy_versions`` — what "they consented" actually referred to.
  Consent to a notice nobody can reproduce is not consent.
* ``grievances`` — DPDP requires a route for complaints, and it has to live
  somewhere erasure can reach. The issue-report Google Sheet cannot.
* ``deleted_user_tombstones`` — survives the purge on purpose, so that restoring
  a database backup does not silently resurrect someone who asked to be erased.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Index,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class ConsentPurpose(str, enum.Enum):
    """Consent is per-PURPOSE, not one blanket yes.

    ``account_and_advisory`` is the one purpose the product cannot run without;
    the rest must be independently refusable, or the consent is not free.
    """

    account_and_advisory = "account_and_advisory"
    cas_ingestion = "cas_ingestion"
    llm_processing = "llm_processing"
    analytics = "analytics"
    marketing_comms = "marketing_comms"


#: Purposes a user may decline while still holding an account. Everything not
#: listed is necessary for the service itself.
OPTIONAL_PURPOSES: frozenset[ConsentPurpose] = frozenset(
    {
        ConsentPurpose.cas_ingestion,
        ConsentPurpose.llm_processing,
        ConsentPurpose.analytics,
        ConsentPurpose.marketing_comms,
    }
)


class ConsentRecord(Base):
    """One immutable entry: "at T, user U said yes/no to purpose P under notice V"."""

    __tablename__ = "consent_records"
    __table_args__ = (
        # The hot read is "what does this user's latest row per purpose say".
        Index(
            "ix_consent_records_user_purpose_recorded",
            "user_id",
            "purpose",
            "recorded_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )

    purpose: Mapped[ConsentPurpose] = mapped_column(
        SAEnum(ConsentPurpose, name="consent_purpose_enum", create_constraint=False),
        nullable=False,
    )
    granted: Mapped[bool] = mapped_column(Boolean, nullable=False)
    policy_version: Mapped[str] = mapped_column(String(32), nullable=False)

    # Evidence of the act itself. Kept deliberately coarse — a truncated IP and
    # the user agent are what makes a consent record defensible without turning
    # the ledger into its own tracking surface.
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="web")
    ip: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )


class PrivacyPolicyVersion(Base):
    """The notice a ``ConsentRecord.policy_version`` points at."""

    __tablename__ = "privacy_policy_versions"

    version: Mapped[str] = mapped_column(String(32), primary_key=True)
    effective_from: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    # Hash rather than the text: proves the notice has not been edited under a
    # user's feet, without this table becoming a CMS.
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class GrievanceStatus(str, enum.Enum):
    open = "open"
    in_progress = "in_progress"
    resolved = "resolved"


class Grievance(Base):
    """A data-protection complaint or rights request that needs a human.

    Separate from the issue-report register on purpose: that one is a Google
    Sheet, which no erasure job can reach.
    """

    __tablename__ = "grievances"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    category: Mapped[str] = mapped_column(String(40), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[GrievanceStatus] = mapped_column(
        SAEnum(GrievanceStatus, name="grievance_status_enum", create_constraint=False),
        nullable=False,
        default=GrievanceStatus.open,
    )
    resolution_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    resolved_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class DeletedUserTombstone(Base):
    """Proof an erasure happened, holding nothing that identifies the person.

    Deliberately NOT FK'd to ``users`` — the row it refers to is gone, and a
    foreign key would either block the purge or cascade the evidence away with
    it. The user id is kept because it is a random UUID we minted: on its own it
    identifies nobody, but it lets a restore re-apply the deletion instead of
    quietly bringing the account back.
    """

    __tablename__ = "deleted_user_tombstones"

    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    purged_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Counts only — useful for answering "was the erasure complete?" without
    # retaining anything about who they were.
    rows_deleted: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    reason: Mapped[str] = mapped_column(
        String(40), nullable=False, default="user_request"
    )
