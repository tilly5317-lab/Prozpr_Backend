"""Latest per-fund holdings snapshot used by holdings UI."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Optional

from sqlalchemy import Date, DateTime, ForeignKey, Integer, Numeric, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class UserMfLatestSnapshot(Base):
    """Denormalized latest MF holdings row per (user, scheme_code)."""

    __tablename__ = "user_mf_latest_snapshot"
    __table_args__ = (
        UniqueConstraint("user_id", "scheme_code", name="uq_user_mf_latest_snapshot_user_scheme"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    scheme_code: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    isin: Mapped[Optional[str]] = mapped_column(String(12), nullable=True, index=True)
    fund_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    amc_name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    category: Mapped[Optional[str]] = mapped_column(String(50), nullable=True, index=True)
    sub_category: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    sub_group: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    invested_amount: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False, server_default="0")
    current_units: Mapped[float] = mapped_column(Numeric(18, 4), nullable=False, server_default="0")
    avg_nav: Mapped[Optional[float]] = mapped_column(Numeric(12, 4), nullable=True)
    current_nav: Mapped[Optional[float]] = mapped_column(Numeric(12, 4), nullable=True)
    current_value: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False, server_default="0")
    unrealized_pnl: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False, server_default="0")
    absolute_return_pct: Mapped[Optional[float]] = mapped_column(Numeric(10, 4), nullable=True)
    xirr_pct: Mapped[Optional[float]] = mapped_column(Numeric(10, 4), nullable=True)
    portfolio_weight_pct: Mapped[Optional[float]] = mapped_column(Numeric(10, 4), nullable=True)
    return_1y_pct: Mapped[Optional[float]] = mapped_column(Numeric(10, 4), nullable=True)
    return_3y_pct: Mapped[Optional[float]] = mapped_column(Numeric(10, 4), nullable=True)
    return_5y_pct: Mapped[Optional[float]] = mapped_column(Numeric(10, 4), nullable=True)

    first_investment_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    last_transaction_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    nav_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    transactions_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    folio_number: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
