"""SQLAlchemy ORM model — `index_tri_history.py`.

Daily NSE index Total Return Index (TRI) history. Standalone (no FK): index
data is independent of the fund universe. Stores both Gross TRI (``tri_value``,
the benchmark) and Net TRI (``ntr_value``). Multi-index ready via ``index_name``.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Optional

from sqlalchemy import Date, DateTime, Numeric, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class IndexTriHistory(Base):
    """Daily TRI feed for an NSE index (e.g. NIFTY 50)."""

    __tablename__ = "index_tri_history"
    __table_args__ = (
        UniqueConstraint("index_name", "tri_date", name="uq_index_tri_name_date"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    index_name: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    tri_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    tri_value: Mapped[float] = mapped_column(Numeric(14, 4), nullable=False)
    ntr_value: Mapped[Optional[float]] = mapped_column(Numeric(14, 4), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
