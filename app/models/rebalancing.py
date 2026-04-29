"""SQLAlchemy ORM model — `rebalancing.py`.

Holds two row kinds, distinguished by ``recommendation_type``:
- ``ALLOCATION`` rows — goal-based asset allocation outputs (legacy + cache).
- ``REBALANCING_TRADES`` rows — trade-list outputs from the rebalancing engine.

A trade-list row references the allocation row it consumed via
``source_allocation_id`` (audit + cache lookup).
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


class RecommendationType(str, enum.Enum):
    ALLOCATION = "allocation"
    REBALANCING_TRADES = "rebalancing_trades"


class RebalancingRecommendation(Base):
    __tablename__ = "rebalancing_recommendations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    portfolio_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("portfolios.id", ondelete="CASCADE")
    )
    recommendation_type: Mapped[RecommendationType] = mapped_column(
        SAEnum(RecommendationType, name="recommendation_type_enum", create_constraint=True),
        nullable=False,
    )
    source_allocation_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("rebalancing_recommendations.id", ondelete="SET NULL"),
        nullable=True,
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
