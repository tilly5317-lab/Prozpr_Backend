"""Investment objectives, horizon, and owned real estate (1:1 user + 1:N properties)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.domains.profile.models.user_current_property import UserCurrentProperty
    from app.domains.identity.models.user import User


class InvestmentProfile(Base):
    __tablename__ = "investment_profiles"
    __table_args__ = (UniqueConstraint("user_id", name="uq_investment_profiles_user_id"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE")
    )

    objectives: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)
    detailed_goals: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)
    portfolio_value: Mapped[Optional[float]] = mapped_column(Numeric(15, 2), nullable=True)
    target_corpus: Mapped[Optional[float]] = mapped_column(Numeric(15, 2), nullable=True)
    target_timeline: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    retirement_age: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    expected_inflows: Mapped[Optional[float]] = mapped_column(Numeric(15, 2), nullable=True)
    planned_major_expenses: Mapped[Optional[float]] = mapped_column(Numeric(15, 2), nullable=True)
    emergency_fund: Mapped[Optional[float]] = mapped_column(Numeric(15, 2), nullable=True)
    emergency_fund_months: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    liquidity_needs: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    income_needs: Mapped[Optional[float]] = mapped_column(Numeric(15, 2), nullable=True)

    is_multi_phase_horizon: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    phase_description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    total_horizon: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    user: Mapped["User"] = relationship(back_populates="investment_profile")
    current_properties: Mapped[List["UserCurrentProperty"]] = relationship(
        "UserCurrentProperty",
        back_populates="investment_profile",
        primaryjoin="InvestmentProfile.user_id == UserCurrentProperty.user_id",
        foreign_keys="UserCurrentProperty.user_id",
        cascade="all, delete-orphan",
        order_by="UserCurrentProperty.id",
        overlaps="user",
    )
