"""SQLAlchemy ORM model — `mf_aa_import.py`.

Defines a database table mapping, columns, and relationships. Imported by services and Alembic migrations; avoid importing FastAPI or routers from here to prevent circular dependencies.
"""


from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import DateTime, Enum as SAEnum, ForeignKey, Integer, Numeric, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.mf.enums import MfAaImportStatus

if TYPE_CHECKING:
    from app.models.user import User


class MfAaImport(Base):
    """One row per AA payload/meta group (meta + investor identity).

    This table is append-only ingestion/audit context; canonical business logic
    should use normalized rows in ``mf_transactions``.
    """

    __tablename__ = "mf_aa_imports"
    __table_args__ = (UniqueConstraint("req_id", "email", name="uq_mf_aa_import_req_email"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    pan: Mapped[Optional[str]] = mapped_column(String(20), nullable=True, index=True)
    pekrn: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    email: Mapped[Optional[str]] = mapped_column(String(320), nullable=True, index=True)
    mobile: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    from_date: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    to_date: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    req_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    investor_first_name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    investor_middle_name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    investor_last_name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    address_line_1: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    address_line_2: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    address_line_3: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    city: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    district: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    state: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    pincode: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    country: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    source_file: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    status: Mapped[MfAaImportStatus] = mapped_column(
        SAEnum(MfAaImportStatus, name="mf_aa_import_status_enum", create_constraint=True),
        nullable=False,
        default=MfAaImportStatus.RECEIVED,
        index=True,
    )
    normalized_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    failure_reason: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    imported_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    user: Mapped[Optional["User"]] = relationship(back_populates="mf_aa_imports")
    summaries: Mapped[List["MfAaSummary"]] = relationship(
        back_populates="aa_import", cascade="all, delete-orphan"
    )
    transactions: Mapped[List["MfAaTransaction"]] = relationship(
        back_populates="aa_import", cascade="all, delete-orphan"
    )


class MfAaSummary(Base):
    """Holding snapshot rows from AA summary CSV (`dtSummary`)."""

    __tablename__ = "mf_aa_summaries"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    aa_import_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("mf_aa_imports.id", ondelete="CASCADE"), index=True
    )
    row_no: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    amc: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    amc_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    asset_type: Mapped[Optional[str]] = mapped_column(String(30), nullable=True, index=True)
    broker_code: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    broker_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    closing_balance: Mapped[Optional[float]] = mapped_column(Numeric(18, 3), nullable=True)
    cost_value: Mapped[Optional[float]] = mapped_column(Numeric(15, 2), nullable=True)
    decimal_amount: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    decimal_nav: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    decimal_units: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    folio: Mapped[Optional[str]] = mapped_column(String(40), nullable=True, index=True)
    is_demat: Mapped[Optional[str]] = mapped_column(String(5), nullable=True)
    isin: Mapped[Optional[str]] = mapped_column(String(20), nullable=True, index=True)
    kyc_status: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    last_nav_date: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    last_trxn_date: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    market_value: Mapped[Optional[float]] = mapped_column(Numeric(15, 2), nullable=True)
    nav: Mapped[Optional[float]] = mapped_column(Numeric(12, 4), nullable=True)
    nominee_status: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    opening_bal: Mapped[Optional[float]] = mapped_column(Numeric(18, 3), nullable=True)
    rta_code: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    scheme: Mapped[Optional[str]] = mapped_column(String(20), nullable=True, index=True)
    scheme_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    tax_status: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    aa_import: Mapped["MfAaImport"] = relationship(back_populates="summaries")


class MfAaTransaction(Base):
    """Transaction rows from AA transaction CSV (`dtTransaction`)."""

    __tablename__ = "mf_aa_transactions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    aa_import_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("mf_aa_imports.id", ondelete="CASCADE"), index=True
    )
    row_no: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    amc: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    amc_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    check_digit: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    folio: Mapped[Optional[str]] = mapped_column(String(40), nullable=True, index=True)
    isin: Mapped[Optional[str]] = mapped_column(String(20), nullable=True, index=True)
    posted_date: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    purchase_price: Mapped[Optional[float]] = mapped_column(Numeric(12, 4), nullable=True)
    scheme: Mapped[Optional[str]] = mapped_column(String(20), nullable=True, index=True)
    scheme_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    stamp_duty: Mapped[Optional[float]] = mapped_column(Numeric(12, 2), nullable=True)
    stt_tax: Mapped[Optional[float]] = mapped_column(Numeric(12, 2), nullable=True)
    tax: Mapped[Optional[float]] = mapped_column(Numeric(12, 2), nullable=True)
    total_tax: Mapped[Optional[float]] = mapped_column(Numeric(12, 2), nullable=True)
    trxn_amount: Mapped[Optional[float]] = mapped_column(Numeric(15, 2), nullable=True)
    trxn_charge: Mapped[Optional[float]] = mapped_column(Numeric(12, 2), nullable=True)
    trxn_date: Mapped[Optional[str]] = mapped_column(String(20), nullable=True, index=True)
    trxn_desc: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    trxn_mode: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    trxn_type_flag: Mapped[Optional[str]] = mapped_column(String(20), nullable=True, index=True)
    trxn_units: Mapped[Optional[float]] = mapped_column(Numeric(18, 3), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    aa_import: Mapped["MfAaImport"] = relationship(back_populates="transactions")
