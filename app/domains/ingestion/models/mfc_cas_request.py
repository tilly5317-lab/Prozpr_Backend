"""SQLAlchemy ORM model — `mfc_cas_request.py`.

One row per MF Central CAS consent request: the server-side half of a flow whose
middle happens entirely on MFC's website.

The flow is unavoidably stateful across two HTTP calls that are minutes apart and
separated by the investor leaving our app:

    POST /mfc-cas/start   -> MFC mints (reqId, otpRef); we hand the investor a
                             redirect URL and remember this row
    ...investor consents on MFC, downloads a QR, comes back...
    POST /mfc-cas/validate-qr -> we need that SAME reqId + clientRefNo to
                                 exchange the QR for the statement

``client_ref_no`` is our idempotency key and MFC requires it to be unique across
every request we ever make, so it is generated here and stored, never
recomputed. ``req_id`` is MFC's. Losing either one strands a consent the
investor has already given and cannot re-give without starting over — which is
the entire reason this is a table and not an in-process dict.

Deliberately NOT ``CasScoped``: a request row describes an attempt, not a
statement. It has to stay readable after the statement it produced has been
superseded, otherwise the flow's own history disappears at the next import.

The statement itself is never stored here — it lands through the normal ingest
path (``mf_aa_imports`` + ``cas_uploads``) and this row points at the resulting
``cas_upload_id``.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import (
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
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class MfcCasRequestStatus(str, enum.Enum):
    """How far one consent request got.

    ``INITIATED`` is the honest resting state for most rows: the investor was
    redirected and we cannot observe what happens next, because the consent and
    the QR download happen entirely on MFC. Only their return with a QR moves
    the row forward.
    """

    INITIATED = "initiated"
    IMPORTED = "imported"
    FAILED = "failed"


MFC_CAS_REQUEST_STATUSES: frozenset[str] = frozenset(
    s.value for s in MfcCasRequestStatus
)


class MfcCasRequest(Base):
    """A single MF Central CAS consent request and what became of it."""

    __tablename__ = "mfc_cas_requests"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Ours. Unique across all requests we ever send MFC — they key their own
    # logging on it, and a collision makes two investors' requests
    # indistinguishable in a support conversation with them.
    client_ref_no: Mapped[str] = mapped_column(String(30), nullable=False, unique=True)
    # MFC's. String, not int: their samples show both `3102549` and
    # `2638435-590807058`, and the hyphenated form appears in detailed responses.
    req_id: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    otp_ref: Mapped[Optional[str]] = mapped_column(String(128))

    # What we asked for. PAN is stored because MFC keys the statement on it and a
    # mismatch at validate time is the single most common support question.
    pan: Mapped[Optional[str]] = mapped_column(String(20))
    mobile: Mapped[Optional[str]] = mapped_column(String(20))
    email: Mapped[Optional[str]] = mapped_column(String(320))
    from_date: Mapped[Optional[str]] = mapped_column(String(20))
    to_date: Mapped[Optional[str]] = mapped_column(String(20))

    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=MfcCasRequestStatus.INITIATED.value
    )
    # "summary" | "detailed" — decided by the investor inside MFC's UI, so it is
    # unknown until the QR comes back.
    cas_variant: Mapped[Optional[str]] = mapped_column(String(20))
    error: Mapped[Optional[str]] = mapped_column(Text)

    # Headline results, so the request list is useful without re-reading the
    # statement it produced.
    folios: Mapped[Optional[int]] = mapped_column(Integer)
    schemes: Mapped[Optional[int]] = mapped_column(Integer)
    transactions: Mapped[Optional[int]] = mapped_column(Integer)
    total_value_inr: Mapped[Optional[float]] = mapped_column(Numeric(18, 2))

    cas_upload_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True))
    mf_aa_import_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_mfc_cas_requests_user_created", "user_id", "created_at"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<MfcCasRequest {self.client_ref_no} req={self.req_id} "
            f"status={self.status}>"
        )
