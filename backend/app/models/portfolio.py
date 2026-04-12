"""SQLAlchemy ORM model — `portfolio.py`.

Defines a database table mapping, columns, and relationships. Imported by services and Alembic migrations; avoid importing FastAPI or routers from here to prevent circular dependencies.
"""


from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, Numeric, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.user import User


class Portfolio(Base):
    __tablename__ = "portfolios"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE")
    )

    name: Mapped[str] = mapped_column(String(255), default="Primary", nullable=False)
    total_value: Mapped[float] = mapped_column(Numeric(15, 2), default=0, nullable=False)
    total_invested: Mapped[float] = mapped_column(Numeric(15, 2), default=0, nullable=False)
    total_gain_percentage: Mapped[Optional[float]] = mapped_column(Numeric(7, 2), nullable=True)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    user: Mapped["User"] = relationship(back_populates="portfolios")
    allocations: Mapped[List["PortfolioAllocation"]] = relationship(
        back_populates="portfolio", cascade="all, delete-orphan"
    )
    holdings: Mapped[List["PortfolioHolding"]] = relationship(
        back_populates="portfolio", cascade="all, delete-orphan"
    )
    history: Mapped[List["PortfolioHistory"]] = relationship(
        back_populates="portfolio", cascade="all, delete-orphan"
    )


class PortfolioAllocation(Base):
    """Current computed allocation state for a portfolio.

    This table should be treated as a derived state from holdings/ledger sync,
    not as manual strategy input.
    """

    __tablename__ = "portfolio_allocations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    portfolio_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("portfolios.id", ondelete="CASCADE")
    )

    asset_class: Mapped[str] = mapped_column(String(100), nullable=False)
    allocation_percentage: Mapped[float] = mapped_column(Numeric(5, 2), nullable=False)
    amount: Mapped[float] = mapped_column(Numeric(15, 2), nullable=False)
    performance_percentage: Mapped[Optional[float]] = mapped_column(Numeric(7, 2), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    portfolio: Mapped["Portfolio"] = relationship(back_populates="allocations")


class PortfolioHolding(Base):
    __tablename__ = "portfolio_holdings"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    portfolio_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("portfolios.id", ondelete="CASCADE")
    )

    instrument_name: Mapped[str] = mapped_column(String(255), nullable=False)
    instrument_type: Mapped[str] = mapped_column(String(50), nullable=False)
    ticker_symbol: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    quantity: Mapped[Optional[float]] = mapped_column(Numeric(15, 4), nullable=True)
    average_cost: Mapped[Optional[float]] = mapped_column(Numeric(15, 4), nullable=True)
    current_price: Mapped[Optional[float]] = mapped_column(Numeric(15, 4), nullable=True)
    current_value: Mapped[float] = mapped_column(Numeric(15, 2), nullable=False)
    allocation_percentage: Mapped[Optional[float]] = mapped_column(Numeric(5, 2), nullable=True)
    exchange: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    expense_ratio: Mapped[Optional[float]] = mapped_column(Numeric(5, 4), nullable=True)
    return_1y: Mapped[Optional[float]] = mapped_column(Numeric(7, 2), nullable=True)
    return_3y: Mapped[Optional[float]] = mapped_column(Numeric(7, 2), nullable=True)
    return_5y: Mapped[Optional[float]] = mapped_column(Numeric(7, 2), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    portfolio: Mapped["Portfolio"] = relationship(back_populates="holdings")


class PortfolioHistory(Base):
    __tablename__ = "portfolio_history"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    portfolio_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("portfolios.id", ondelete="CASCADE")
    )

    recorded_date: Mapped[date] = mapped_column(Date, nullable=False)
    total_value: Mapped[float] = mapped_column(Numeric(15, 2), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    portfolio: Mapped["Portfolio"] = relationship(back_populates="history")
