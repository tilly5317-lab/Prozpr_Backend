"""SQLAlchemy ORM model — `investment_profile.py`.

Defines a database table mapping, columns, and relationships. Imported by services and Alembic migrations; avoid importing FastAPI or routers from here to prevent circular dependencies.
"""


from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.user import User


class InvestmentProfile(Base):
    __tablename__ = "investment_profiles"
    __table_args__ = (UniqueConstraint("user_id", name="uq_investment_profiles_user_id"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE")
    )

    # Section 2 - Objectives
    objectives: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)
    detailed_goals: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)
    portfolio_value: Mapped[Optional[float]] = mapped_column(Numeric(15, 2), nullable=True)
    monthly_savings: Mapped[Optional[float]] = mapped_column(Numeric(15, 2), nullable=True)
    target_corpus: Mapped[Optional[float]] = mapped_column(Numeric(15, 2), nullable=True)
    target_timeline: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    annual_income: Mapped[Optional[float]] = mapped_column(Numeric(15, 2), nullable=True)
    retirement_age: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # Section 4 - Financial picture
    investable_assets: Mapped[Optional[float]] = mapped_column(Numeric(15, 2), nullable=True)
    total_liabilities: Mapped[Optional[float]] = mapped_column(Numeric(15, 2), nullable=True)
    property_value: Mapped[Optional[float]] = mapped_column(Numeric(15, 2), nullable=True)
    mortgage_amount: Mapped[Optional[float]] = mapped_column(Numeric(15, 2), nullable=True)
    annual_mortgage_payment: Mapped[Optional[float]] = mapped_column(Numeric(15, 2), nullable=True)
    properties_owned: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    expected_inflows: Mapped[Optional[float]] = mapped_column(Numeric(15, 2), nullable=True)
    regular_outgoings: Mapped[Optional[float]] = mapped_column(Numeric(15, 2), nullable=True)
    planned_major_expenses: Mapped[Optional[float]] = mapped_column(Numeric(15, 2), nullable=True)
    emergency_fund: Mapped[Optional[float]] = mapped_column(Numeric(15, 2), nullable=True)
    emergency_fund_months: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    liquidity_needs: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    income_needs: Mapped[Optional[float]] = mapped_column(Numeric(15, 2), nullable=True)

    # Section 6 - Time horizon
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
