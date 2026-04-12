"""SQLAlchemy ORM model — `risk_profile.py`.

Defines a database table mapping, columns, and relationships. Imported by services and Alembic migrations; avoid importing FastAPI or routers from here to prevent circular dependencies.
"""


from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.user import User

RISK_CATEGORIES = ["Conservative", "Moderately Conservative", "Moderate", "Moderately Aggressive", "Aggressive"]


class RiskProfile(Base):
    __tablename__ = "risk_profiles"
    __table_args__ = (UniqueConstraint("user_id", name="uq_risk_profiles_user_id"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE")
    )

    # 0=Conservative, 1=Moderately Conservative, 2=Moderate, 3=Moderately Aggressive, 4=Aggressive
    risk_level: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # User-declared willingness on 1–10 scale (optional; falls back to mapping from risk_level)
    risk_willingness: Mapped[Optional[float]] = mapped_column(Numeric(5, 2), nullable=True)
    # For effective risk score OSI mapping (public_sector | private_sector | ...)
    occupation_type: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    risk_capacity: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    investment_experience: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    investment_horizon: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    drop_reaction: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    max_drawdown: Mapped[Optional[float]] = mapped_column(Numeric(5, 2), nullable=True)
    comfort_assets: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    user: Mapped["User"] = relationship(back_populates="risk_profile")

    @property
    def risk_category(self) -> Optional[str]:
        if self.risk_level is not None and 0 <= self.risk_level <= 4:
            return RISK_CATEGORIES[self.risk_level]
        return None
