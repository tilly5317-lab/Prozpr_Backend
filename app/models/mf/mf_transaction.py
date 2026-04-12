"""SQLAlchemy ORM model — `mf_transaction.py`.

Defines a database table mapping, columns, and relationships. Imported by services and Alembic migrations; avoid importing FastAPI or routers from here to prevent circular dependencies.
"""


from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    Date,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

from app.models.mf.enums import MfTransactionSource, MfTransactionType

if TYPE_CHECKING:
    from app.models.mf.mf_sip_mandate import MfSipMandate
    from app.models.user import User


class MfTransaction(Base):
    __tablename__ = "mf_transactions"
    __table_args__ = (
        UniqueConstraint(
            "source_system",
            "source_txn_fingerprint",
            name="uq_mf_txn_source_fingerprint",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    scheme_code: Mapped[str] = mapped_column(
        String(20),
        ForeignKey("mf_fund_metadata.scheme_code", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    sip_mandate_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("mf_sip_mandates.id", ondelete="SET NULL"), nullable=True
    )
    folio_number: Mapped[str] = mapped_column(String(30), nullable=False)
    transaction_type: Mapped[MfTransactionType] = mapped_column(
        SAEnum(MfTransactionType, name="mf_transaction_type_enum", create_constraint=True),
        nullable=False,
    )
    transaction_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    units: Mapped[float] = mapped_column(Numeric(18, 4), nullable=False)
    nav: Mapped[float] = mapped_column(Numeric(12, 4), nullable=False)
    amount: Mapped[float] = mapped_column(Numeric(15, 2), nullable=False)
    stamp_duty: Mapped[Optional[float]] = mapped_column(Numeric(10, 2), nullable=True)
    source_system: Mapped[MfTransactionSource] = mapped_column(
        SAEnum(MfTransactionSource, name="mf_transaction_source_enum", create_constraint=True),
        nullable=False,
        default=MfTransactionSource.MANUAL,
        index=True,
    )
    source_import_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("mf_aa_imports.id", ondelete="SET NULL"), nullable=True, index=True
    )
    source_txn_fingerprint: Mapped[Optional[str]] = mapped_column(
        String(128), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    user: Mapped["User"] = relationship(back_populates="mf_transactions")
    sip_mandate: Mapped[Optional["MfSipMandate"]] = relationship(back_populates="transactions")
