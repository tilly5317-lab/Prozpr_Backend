"""SQLAlchemy ORM model — `other_investment.py`.

Defines a database table mapping, columns, and relationships. Imported by services and Alembic migrations; avoid importing FastAPI or routers from here to prevent circular dependencies.
"""


from __future__ import annotations

import enum
import uuid
from datetime import date, datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import Date, DateTime, Enum as SAEnum, ForeignKey, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.user import User


class OtherInvestmentStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    MATURED = "MATURED"
    WITHDRAWN = "WITHDRAWN"
    CLOSED = "CLOSED"


class OtherInvestment(Base):
    __tablename__ = "other_investments"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )

    investment_type: Mapped[str] = mapped_column(String(50), nullable=False)
    investment_name: Mapped[str] = mapped_column(String(200), nullable=False)
    present_value: Mapped[float] = mapped_column(Numeric(15, 2), nullable=False)
    as_of_date: Mapped[date] = mapped_column(Date, nullable=False)
    maturity_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    status: Mapped[OtherInvestmentStatus] = mapped_column(
        SAEnum(OtherInvestmentStatus, name="other_investment_status_enum", create_constraint=True),
        nullable=False,
        default=OtherInvestmentStatus.ACTIVE,
    )
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    user: Mapped["User"] = relationship(back_populates="other_investments")
