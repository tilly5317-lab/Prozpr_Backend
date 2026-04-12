"""SQLAlchemy ORM model — `personal_finance_profile.py`.

Defines a database table mapping, columns, and relationships. Imported by services and Alembic migrations; avoid importing FastAPI or routers from here to prevent circular dependencies.
"""


from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import DateTime, ForeignKey, Numeric, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.user import User


class PersonalFinanceProfile(Base):
    __tablename__ = "personal_finance_profiles"
    __table_args__ = (UniqueConstraint("user_id", name="uq_personal_finance_profiles_user_id"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE")
    )

    # Financially-oriented onboarding/profile fields
    selected_goals: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)
    custom_goals: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)
    investment_horizon: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    annual_income_min: Mapped[Optional[float]] = mapped_column(Numeric(15, 2), nullable=True)
    annual_income_max: Mapped[Optional[float]] = mapped_column(Numeric(15, 2), nullable=True)
    annual_expense_min: Mapped[Optional[float]] = mapped_column(Numeric(15, 2), nullable=True)
    annual_expense_max: Mapped[Optional[float]] = mapped_column(Numeric(15, 2), nullable=True)
    wealth_sources: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)
    personal_values: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    user: Mapped["User"] = relationship(back_populates="personal_finance_profile")
