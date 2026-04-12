"""SQLAlchemy ORM model — `rebalancing.py`.

Defines a database table mapping, columns, and relationships. Imported by services and Alembic migrations; avoid importing FastAPI or routers from here to prevent circular dependencies.
"""


from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Enum as SAEnum, ForeignKey, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class RebalancingStatus(str, enum.Enum):
    pending = "pending"
    approved = "approved"
    executed = "executed"
    rejected = "rejected"


class RebalancingRecommendation(Base):
    """Action-layer record: proposed rebalance plan and lifecycle status.

    Recommendations are not source-of-truth for portfolio state.
    """

    __tablename__ = "rebalancing_recommendations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    portfolio_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("portfolios.id", ondelete="CASCADE")
    )

    status: Mapped[RebalancingStatus] = mapped_column(
        SAEnum(RebalancingStatus, name="rebalancing_status_enum", create_constraint=True),
        default=RebalancingStatus.pending,
    )
    recommendation_data: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    reason: Mapped[Optional[str]] = mapped_column(nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
