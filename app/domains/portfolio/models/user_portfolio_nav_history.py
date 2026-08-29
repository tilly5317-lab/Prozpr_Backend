"""Per-user daily portfolio NAV history.

One row per (user_id, recorded_date). Computed and cached by
``app.domains.portfolio.services.nav_history_service.recompute_user_nav_history`` from each
user's mutual-fund holdings (units × backcast NAV using the holding's stated
return_1y / return_3y / return_5y). Used by the portfolio dashboard chart.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Numeric, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.domains.ingestion.models.cas_upload import CasScoped


class UserPortfolioNavHistory(CasScoped, Base):
    __tablename__ = "user_portfolio_nav_history"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "recorded_date", name="uq_user_nav_history_user_date"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    recorded_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    total_value: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False)
    total_invested: Mapped[float] = mapped_column(
        Numeric(18, 2), nullable=False, server_default="0"
    )
    gain_percentage: Mapped[float] = mapped_column(
        Numeric(10, 4), nullable=False, server_default="0"
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
