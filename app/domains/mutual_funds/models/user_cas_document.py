"""SQLAlchemy ORM model — `user_cas_document.py`.

A user's uploaded CAS statement PDF, kept permanently in the private S3
bucket (`user-cas/{user_id}/{id}.pdf`) and listed on their profile.

Deliberately SEPARATE from ``mf_aa_imports`` (the parsed audit trail) and from
``cas_uploads`` (the snapshot header): this is the FILE. It carries no
``cas_upload_id`` and is not snapshot-scoped — the archive outlives the ingest
that read it, and a snapshot points at its PDF through
``cas_uploads.cas_document_id``. This table must also never be added to
``reset_user_financial_data``'s delete list.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class UserCasDocument(Base):
    __tablename__ = "user_cas_documents"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # S3 object key in CAMS_STAGE_S3_BUCKET; the PDF stays protected by the
    # user's own statement password on top of SSE-KMS at rest.
    s3_key: Mapped[str] = mapped_column(String(255), nullable=False)
    source_filename: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    file_size_bytes: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    cas_type: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    file_type: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    statement_from: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    statement_to: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    folios: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    schemes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    transactions: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
