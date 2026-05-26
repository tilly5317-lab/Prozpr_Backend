"""Owned real estate rows — child of ``investment_profiles`` (same ``user_id``)."""

from __future__ import annotations

import uuid
from datetime import date
from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    Numeric,
    Text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.profile.investment_profile import InvestmentProfile


class UserCurrentProperty(Base):
    __tablename__ = "user_current_properties"
    __table_args__ = (
        CheckConstraint(
            "has_mortgage = FALSE OR (mortgage_emi IS NOT NULL AND mortgage_end_date IS NOT NULL)",
            name="chk_mortgage_fields_present",
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
    has_mortgage: Mapped[bool] = mapped_column(Boolean, nullable=False)
    mortgage_emi: Mapped[Optional[float]] = mapped_column(Numeric(15, 2), nullable=True)
    mortgage_end_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)

    investment_profile: Mapped["InvestmentProfile"] = relationship(
        "InvestmentProfile",
        back_populates="current_properties",
        primaryjoin="UserCurrentProperty.user_id == InvestmentProfile.user_id",
        foreign_keys=[user_id],
    )
