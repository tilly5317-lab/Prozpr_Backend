"""SQLAlchemy ORM model — unified ``goals`` table for cashflow + legacy allocation.

Cashflow engine fields follow ``viewer_db_schema.md`` (retirement, property,
education, marriage, custom). Legacy allocation columns remain nullable for
migration from the prior goal shape.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import List, Optional

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.domains.cashflow.models.enums import CashflowGoalType
from app.domains.goals.models.enums import GoalPriority, GoalStatus


class FinancialGoal(Base):
    __tablename__ = "goals"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )

    # --- Cashflow-unified fields (added later; nullable for legacy rows) ---
    name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    goal_type: Mapped[Optional[CashflowGoalType]] = mapped_column(
        SAEnum(
            CashflowGoalType,
            name="cashflow_goal_type_enum",
            values_callable=lambda e: [m.value for m in e],
            create_constraint=False,
        ),
        nullable=True,
    )
    goal_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)

    goal_value_pv: Mapped[Optional[float]] = mapped_column(
        Numeric(18, 2), nullable=True
    )
    goal_value_fv: Mapped[Optional[float]] = mapped_column(
        Numeric(18, 2), nullable=True
    )
    inflation_rate: Mapped[Optional[float]] = mapped_column(
        Numeric(7, 6), nullable=True
    )

    target_pv: Mapped[Optional[float]] = mapped_column(Numeric(18, 2), nullable=True)
    target_fv: Mapped[Optional[float]] = mapped_column(Numeric(18, 2), nullable=True)
    is_downpayment_only: Mapped[Optional[bool]] = mapped_column(
        Boolean, nullable=True, server_default="false"
    )
    upfront_amount: Mapped[Optional[float]] = mapped_column(
        Numeric(18, 2), nullable=True
    )
    downpayment_pct: Mapped[Optional[float]] = mapped_column(
        Numeric(7, 6), nullable=True
    )
    inflation_annual: Mapped[Optional[float]] = mapped_column(
        Numeric(7, 6), nullable=True
    )
    mortgage_tenure_years: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    mortgage_interest_annual: Mapped[Optional[float]] = mapped_column(
        Numeric(7, 6), nullable=True
    )

    date_of_birth: Mapped[Optional[date]] = mapped_column(Date, nullable=True)

    # --- Legacy allocation / onboarding ---
    goal_name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    present_value_amount: Mapped[Optional[float]] = mapped_column(
        Numeric(15, 2), nullable=True
    )
    target_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    priority: Mapped[Optional[GoalPriority]] = mapped_column(
        SAEnum(GoalPriority, name="goal_priority_enum_v2", create_constraint=False),
        nullable=True,
    )
    status: Mapped[Optional[GoalStatus]] = mapped_column(
        SAEnum(GoalStatus, name="goal_status_enum_v2", create_constraint=False),
        nullable=True,
        default=GoalStatus.ACTIVE,
    )
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    time_to_goal_months: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    amount_needed: Mapped[Optional[float]] = mapped_column(
        Numeric(18, 2), nullable=True
    )
    goal_priority: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    investment_goal: Mapped[Optional[str]] = mapped_column(String(60), nullable=True)

    # Per-goal monthly SIP the user plans to contribute toward this goal.
    monthly_contribution: Mapped[Optional[float]] = mapped_column(
        Numeric(18, 2), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    user = relationship("User", back_populates="financial_goals")
    contributions: Mapped[List["GoalContribution"]] = relationship(
        back_populates="goal", cascade="all, delete-orphan"
    )
    holdings: Mapped[List["GoalHolding"]] = relationship(
        back_populates="goal", cascade="all, delete-orphan"
    )

    @property
    def display_name(self) -> str:
        """Prefer canonical ``name``; fall back to legacy ``goal_name``."""
        return self.name or self.goal_name or ""
