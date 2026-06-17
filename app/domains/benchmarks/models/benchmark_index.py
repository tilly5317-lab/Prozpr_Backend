"""SQLAlchemy ORM model — `benchmark_index.py`.

Catalogue (dimension) of market benchmark indices, e.g. NIFTY 50, NIFTY 100.
One row per index; the daily EOD values live in ``benchmark_index_value`` keyed
by FK. ``code`` is the canonical identifier the niftyindices fetcher uses
(e.g. "NIFTY 50"). The thrice-daily scheduler refreshes only ``is_active`` rows.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import List, Optional, TYPE_CHECKING

from sqlalchemy import Boolean, Date, DateTime, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.domains.benchmarks.models.benchmark_index_value import BenchmarkIndexValue


class BenchmarkIndex(Base):
    """One market benchmark index (catalogue row)."""

    __tablename__ = "benchmark_index"
    __table_args__ = (
        UniqueConstraint("code", name="uq_benchmark_index_code"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # Canonical code the data provider expects (e.g. "NIFTY 50").
    code: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    display_name: Mapped[str] = mapped_column(String(100), nullable=False)
    short_name: Mapped[str] = mapped_column(String(50), nullable=False)
    provider: Mapped[str] = mapped_column(
        String(50), nullable=False, server_default="NSE"
    )
    # EQUITY / DEBT / OTHERS — aligns with the asset-class SSOT.
    asset_class: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="EQUITY"
    )
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    earliest_available: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
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

    values: Mapped[List["BenchmarkIndexValue"]] = relationship(
        back_populates="index",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
