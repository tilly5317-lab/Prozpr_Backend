"""SQLAlchemy ORM model — `effective_risk_assessment.py`.

Defines a database table mapping, columns, and relationships. Imported by services and Alembic migrations; avoid importing FastAPI or routers from here to prevent circular dependencies.
"""


from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any, Optional

from sqlalchemy import DateTime, ForeignKey, Numeric, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.user import User


class EffectiveRiskAssessment(Base):
    """
    One row per user: latest effective risk computation (inputs + full calculation JSON).

    Recomputed when profile/finance/investing inputs change or on scheduled events (e.g. birthday).
    """

    __tablename__ = "effective_risk_assessments"
    __table_args__ = ()

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        unique=True,
        index=True,
    )

    step_name: Mapped[str] = mapped_column(String(64), default="risk_profile", nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    calculations: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    output: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    effective_risk_score: Mapped[Optional[float]] = mapped_column(Numeric(7, 4), nullable=True)
    risk_capacity_score: Mapped[Optional[float]] = mapped_column(Numeric(7, 4), nullable=True)
    risk_willingness: Mapped[Optional[float]] = mapped_column(Numeric(7, 4), nullable=True)

    trigger_reason: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    user: Mapped["User"] = relationship(back_populates="effective_risk_assessment")
