"""SQLAlchemy ORM model — `financial_goal.py`.

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
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

from app.models.goals.enums import GoalPriority, GoalStatus, GoalType

if TYPE_CHECKING:
    from app.models.goals.goal_contribution import GoalContribution
    from app.models.goals.goal_holding import GoalHolding


class FinancialGoal(Base):
    __tablename__ = "goals"
    __table_args__ = (
        CheckConstraint("present_value_amount > 0", name="ck_goals_present_value_positive"),
        CheckConstraint(
            "inflation_rate >= 0 AND inflation_rate <= 50", name="ck_goals_inflation_range"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )

    goal_name: Mapped[str] = mapped_column(String(100), nullable=False)
    goal_type: Mapped[GoalType] = mapped_column(
        SAEnum(GoalType, name="goal_type_enum", create_constraint=True),
        nullable=False,
        default=GoalType.OTHER,
    )
    present_value_amount: Mapped[float] = mapped_column(Numeric(15, 2), nullable=False)
    inflation_rate: Mapped[float] = mapped_column(
        Numeric(5, 2), nullable=False, server_default="6.00"
    )
    target_date: Mapped[date] = mapped_column(Date, nullable=False)
    priority: Mapped[GoalPriority] = mapped_column(
        SAEnum(GoalPriority, name="goal_priority_enum_v2", create_constraint=True),
        nullable=False,
        default=GoalPriority.PRIMARY,
    )
    status: Mapped[GoalStatus] = mapped_column(
        SAEnum(GoalStatus, name="goal_status_enum_v2", create_constraint=True),
        nullable=False,
        default=GoalStatus.ACTIVE,
    )
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    user = relationship("User", back_populates="financial_goals")
    contributions: Mapped[List["GoalContribution"]] = relationship(
        back_populates="goal", cascade="all, delete-orphan"
    )
    holdings: Mapped[List["GoalHolding"]] = relationship(
        back_populates="goal", cascade="all, delete-orphan"
    )
