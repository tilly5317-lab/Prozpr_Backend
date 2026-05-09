"""Lean SQLAlchemy ORM model for goal-based allocation persistence."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any, Optional

from sqlalchemy import DateTime, ForeignKey, Numeric, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.chat import ChatSession
    from app.models.user import User


class GoalAllocationRecommendation(Base):
    __tablename__ = "goal_allocation_recommendations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    portfolio_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("portfolios.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    input_payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    output_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    total_investable_amount: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False)
    equity_amount: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False)
    debt_amount: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False)
    others_amount: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False)
    equity_pct: Mapped[float] = mapped_column(Numeric(7, 2), nullable=False)
    debt_pct: Mapped[float] = mapped_column(Numeric(7, 2), nullable=False)
    others_pct: Mapped[float] = mapped_column(Numeric(7, 2), nullable=False)
    suggested_funds: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    suggested_funds_total_amount: Mapped[float] = mapped_column(
        Numeric(18, 2), nullable=False, default=0
    )
    chat_session_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("chat_sessions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    user: Mapped["User"] = relationship(back_populates="goal_allocation_recommendations")
    chat_session: Mapped[Optional["ChatSession"]] = relationship()
