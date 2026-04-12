"""SQLAlchemy ORM model — `investment_constraint.py`.

Defines a database table mapping, columns, and relationships. Imported by services and Alembic migrations; avoid importing FastAPI or routers from here to prevent circular dependencies.
"""


from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.user import User


class InvestmentConstraint(Base):
    __tablename__ = "investment_constraints"
    __table_args__ = (UniqueConstraint("user_id", name="uq_investment_constraints_user_id"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE")
    )

    permitted_assets: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)
    prohibited_instruments: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)
    is_leverage_allowed: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    is_derivatives_allowed: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    diversification_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    user: Mapped["User"] = relationship(back_populates="investment_constraint")
    allocation_constraints: Mapped[List["AssetAllocationConstraint"]] = relationship(
        back_populates="investment_constraint", cascade="all, delete-orphan"
    )
