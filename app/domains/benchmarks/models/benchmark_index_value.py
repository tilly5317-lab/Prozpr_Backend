"""SQLAlchemy ORM model — `benchmark_index_value.py`.

Daily end-of-day (EOD) value for one benchmark index. ``tri_value`` is the gross
Total Return Index level (used as the return benchmark); ``ntr_value`` the net
TRI; ``pr_value`` the price-return close (reserved, not yet populated). Keyed by
FK to ``benchmark_index`` + ``value_date``, unique per (index, date). The value
for date X is X's EOD close — so the latest available row for "today" is
yesterday's EOD until tonight's close publishes (natural 1-day lag).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Optional, TYPE_CHECKING

from sqlalchemy import (
    Date,
    DateTime,
    ForeignKey,
    Numeric,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.domains.benchmarks.models.benchmark_index import BenchmarkIndex


class BenchmarkIndexValue(Base):
    """Daily EOD value row for a benchmark index."""

    __tablename__ = "benchmark_index_value"
    __table_args__ = (
        UniqueConstraint(
            "benchmark_index_id", "value_date", name="uq_benchmark_value_index_date"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    benchmark_index_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("benchmark_index.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    value_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    tri_value: Mapped[float] = mapped_column(Numeric(14, 4), nullable=False)
    ntr_value: Mapped[Optional[float]] = mapped_column(Numeric(14, 4), nullable=True)
    pr_value: Mapped[Optional[float]] = mapped_column(Numeric(14, 4), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    index: Mapped["BenchmarkIndex"] = relationship(back_populates="values")
