"""SQLAlchemy ORM model — `user_current_property.py`.

User-owned existing properties with optional mortgage state. Conceptually an
extension of `OtherInvestment` specialised for real-estate cashflow inputs;
kept as its own table because property-specific columns (mortgage EMI, end
date) don't fit the generic `OtherInvestment` shape. Mirrors `CurrentProperty`
in `AI_Agents/src/cashflow_statement/models.py`.
"""


from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    Text,
    func,
    text as sa_text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.user import User


class UserCurrentProperty(Base):
    __tablename__ = "user_current_properties"
    __table_args__ = (
        CheckConstraint(
            "property_value IS NULL OR property_value >= 0",
            name="ck_user_curr_prop_value_nonneg",
        ),
        CheckConstraint(
            "mortgage_emi IS NULL OR mortgage_emi >= 0",
            name="ck_user_curr_prop_emi_nonneg",
        ),
        CheckConstraint(
            "has_mortgage = FALSE OR (mortgage_emi IS NOT NULL AND mortgage_end_date IS NOT NULL)",
            name="ck_mortgage_fields_present",
        ),
        Index(
            "uq_user_current_properties_name",
            "user_id",
            func.lower(sa_text("name")),
            unique=True,
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    property_value: Mapped[Optional[float]] = mapped_column(Numeric(18, 2), nullable=True)
    has_mortgage: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    mortgage_emi: Mapped[Optional[float]] = mapped_column(Numeric(15, 2), nullable=True)
    mortgage_end_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    user: Mapped["User"] = relationship(back_populates="current_properties")
