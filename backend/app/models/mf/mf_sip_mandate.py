"""SQLAlchemy ORM model — `mf_sip_mandate.py`.

Defines a database table mapping, columns, and relationships. Imported by services and Alembic migrations; avoid importing FastAPI or routers from here to prevent circular dependencies.
"""


from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

from app.models.mf.enums import MfSipFrequency, MfSipStatus, MfStepupFrequency

if TYPE_CHECKING:
    from app.models.user import User


class MfSipMandate(Base):
    __tablename__ = "mf_sip_mandates"
    __table_args__ = (
        CheckConstraint("debit_day >= 1 AND debit_day <= 28", name="ck_mf_sip_debit_day"),
        CheckConstraint("sip_amount > 0", name="ck_mf_sip_amount_positive"),
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
    )
    folio_number: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    sip_amount: Mapped[float] = mapped_column(Numeric(15, 2), nullable=False)
    frequency: Mapped[MfSipFrequency] = mapped_column(
        SAEnum(MfSipFrequency, name="mf_sip_frequency_enum", create_constraint=True),
        nullable=False,
        default=MfSipFrequency.MONTHLY,
    )
    debit_day: Mapped[int] = mapped_column(Integer, nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    stepup_amount: Mapped[Optional[float]] = mapped_column(Numeric(15, 2), nullable=True)
    stepup_percentage: Mapped[Optional[float]] = mapped_column(Numeric(5, 2), nullable=True)
    stepup_frequency: Mapped[Optional[MfStepupFrequency]] = mapped_column(
        SAEnum(MfStepupFrequency, name="mf_stepup_frequency_enum", create_constraint=True),
        nullable=True,
    )
    status: Mapped[MfSipStatus] = mapped_column(
        SAEnum(MfSipStatus, name="mf_sip_status_enum", create_constraint=True),
        nullable=False,
        default=MfSipStatus.ACTIVE,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    user: Mapped["User"] = relationship(back_populates="mf_sip_mandates")
    transactions: Mapped[List["MfTransaction"]] = relationship(back_populates="sip_mandate")
