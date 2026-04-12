"""SQLAlchemy ORM model — `goal_holding.py`.

Defines a database table mapping, columns, and relationships. Imported by services and Alembic migrations; avoid importing FastAPI or routers from here to prevent circular dependencies.
"""


from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class GoalHolding(Base):
    __tablename__ = "goal_holdings"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    goal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("goals.id", ondelete="CASCADE"), index=True
    )

    fund_name: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    invested_amount: Mapped[float] = mapped_column(Numeric(15, 2), nullable=False)
    current_value: Mapped[Optional[float]] = mapped_column(Numeric(15, 2), nullable=True)
    gain_percentage: Mapped[Optional[float]] = mapped_column(Numeric(7, 2), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    goal: Mapped["FinancialGoal"] = relationship(back_populates="holdings")
