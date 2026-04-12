"""SQLAlchemy ORM model — `fund.py`.

Defines a database table mapping, columns, and relationships. Imported by services and Alembic migrations; avoid importing FastAPI or routers from here to prevent circular dependencies.
"""


from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.user import User


class Fund(Base):
    __tablename__ = "funds"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    short_name: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    ticker_symbol: Mapped[Optional[str]] = mapped_column(String(20), nullable=True, index=True)
    category: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    sector: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    exchange: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    expense_ratio: Mapped[Optional[float]] = mapped_column(Numeric(5, 4), nullable=True)
    exit_load: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    min_investment: Mapped[Optional[float]] = mapped_column(Numeric(15, 2), nullable=True)
    return_1y: Mapped[Optional[float]] = mapped_column(Numeric(7, 2), nullable=True)
    return_3y: Mapped[Optional[float]] = mapped_column(Numeric(7, 2), nullable=True)
    return_5y: Mapped[Optional[float]] = mapped_column(Numeric(7, 2), nullable=True)
    risk_level: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    is_trending: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_house_view: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    user: Mapped[Optional["User"]] = relationship(back_populates="funds")
